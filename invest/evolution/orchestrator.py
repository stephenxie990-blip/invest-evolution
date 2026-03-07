import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .engine import EvolutionEngine
from .llm_optimizer import LLMOptimizer

logger = logging.getLogger(__name__)


class StrategyEvolutionOptimizer:
    """
    策略进化优化器（遗传算法 + LLM）

    亏损周期 → LLM 分析 → 参数调整
    盈利周期 → 近期收益 > 5%时略微激进，否则保守
    """

    def __init__(self):
        self.llm_optimizer  = LLMOptimizer()
        self.best_params: Dict = {}
        self.generation       = 0

    def optimize(
        self,
        cycle_results: List[Dict],
        current_params: Dict,
    ) -> Dict:
        logger.info(f"策略优化第 {self.generation + 1} 代")
        self.generation += 1

        if cycle_results:
            latest = cycle_results[-1]
            if latest.get("return_pct", 0) < 0:
                analysis = self.llm_optimizer.analyze_loss(
                    latest, latest.get("trade_history", [])
                )
                new_params = self.llm_optimizer.generate_strategy_fix(analysis)
                logger.info(f"LLM 参数调整: {new_params}")
                self.best_params = new_params
                return new_params

        params = current_params.copy()
        if len(cycle_results) >= 3:
            avg_ret = sum(r.get("return_pct", 0) for r in cycle_results[-3:]) / 3
            if avg_ret > 5:
                params["position_size"] = min(params.get("position_size", 0.2) * 1.1, 0.30)
            elif avg_ret < 0:
                params["position_size"] = max(params.get("position_size", 0.2) * 0.8, 0.10)

        return params


# ============================================================
# Part 4: 策略库 + 集成层
# ============================================================

@dataclass
class FrozenStrategy:
    """固化策略"""
    name: str
    params: Dict[str, float]
    performance: Dict[str, float]
    frozen_date: str
    win_rate: float = 0.0
    avg_return: float = 0.0


@dataclass
class EnsembleSignal:
    """集成信号"""
    date: str
    action: str          # BUY / SELL / HOLD
    confidence: float
    contributions: Dict[str, any] = field(default_factory=dict)
    reason: str = ""


class StrategyLibrary:
    """策略库（JSON 持久化）"""

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else (
            Path.home() / ".invest_ai" / "strategies"
        )
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.strategies: Dict[str, FrozenStrategy] = {}

    def add_strategy(self, strategy: FrozenStrategy):
        self.strategies[strategy.name] = strategy
        logger.info(f"策略库添加: {strategy.name}")

    def get_strategy(self, name: str) -> Optional[FrozenStrategy]:
        return self.strategies.get(name)

    def get_all_strategies(self) -> List[FrozenStrategy]:
        return list(self.strategies.values())

    def save(self):
        path = self.storage_path / "strategy_library.json"
        data = {
            name: {
                "params": s.params, "performance": s.performance,
                "frozen_date": s.frozen_date, "win_rate": s.win_rate,
                "avg_return": s.avg_return,
            }
            for name, s in self.strategies.items()
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"策略库已保存: {path}")

    def load(self):
        path = self.storage_path / "strategy_library.json"
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for name, s in data.items():
            self.strategies[name] = FrozenStrategy(
                name=name, params=s["params"], performance=s.get("performance", {}),
                frozen_date=s.get("frozen_date", ""), win_rate=s.get("win_rate", 0),
                avg_return=s.get("avg_return", 0),
            )
        logger.info(f"策略库已加载: {len(self.strategies)} 个策略")


class DynamicWeightAllocator:
    """
    动态权重分配器

    近期表现好的策略权重高（线性加权平均）
    单策略权重上限 50%
    """

    def __init__(self, max_weight: float = 0.5, lookback: int = 10):
        self.max_weight        = max_weight
        self.lookback          = lookback
        self.performance_history: Dict[str, List[float]] = {}
        self.current_weights: Dict[str, float] = {}

    def update_performance(self, name: str, return_pct: float):
        hist = self.performance_history.setdefault(name, [])
        hist.append(return_pct)
        self.performance_history[name] = hist[-self.lookback:]

    def calculate_weights(self, strategies: List[FrozenStrategy]) -> Dict[str, float]:
        if not strategies:
            return {}

        raw = {}
        for s in strategies:
            hist = self.performance_history.get(s.name, [])
            if hist:
                w = np.linspace(1, 2, len(hist))
                score = float(np.average(hist, weights=w))
            else:
                score = s.win_rate
            raw[s.name] = max(score, 0)

        total = sum(raw.values())
        norm  = {k: v / total for k, v in raw.items()} if total > 0 else {
            k: 1.0 / len(strategies) for k in raw
        }

        capped = {k: min(v, self.max_weight) for k, v in norm.items()}
        total  = sum(capped.values())
        final  = {k: v / total for k, v in capped.items()} if total > 0 else capped

        self.current_weights = final
        logger.info(f"动态权重: {final}")
        return final


class StrategyEnsemble:
    """
    多策略集成层（加权投票）

    strategy_signals: {策略名: "BUY"/"SELL"/"HOLD"}
    加权投票 → 最终信号 + 置信度
    """

    def __init__(self):
        self.library         = StrategyLibrary()
        self.weight_allocator = DynamicWeightAllocator()

    def add_strategy(self, name: str, params: Dict, performance: Dict):
        self.library.add_strategy(FrozenStrategy(
            name=name, params=params, performance=performance,
            frozen_date=datetime.now().strftime("%Y%m%d"),
            win_rate=performance.get("win_rate", 0),
            avg_return=performance.get("avg_return", 0),
        ))

    def generate_signal(self, strategy_signals: Dict[str, str]) -> EnsembleSignal:
        strategies = self.library.get_all_strategies()
        if not strategies:
            return EnsembleSignal(date=datetime.now().strftime("%Y%m%d"), action="HOLD", confidence=0, reason="策略库为空")

        weights = self.weight_allocator.calculate_weights(strategies)
        votes: Dict[str, float] = {"BUY": 0, "SELL": 0, "HOLD": 0}
        contributions = {}

        for name, sig in strategy_signals.items():
            w = weights.get(name, 0)
            votes[sig] = votes.get(sig, 0) + w
            contributions[name] = {"signal": sig, "weight": w}

        action = max(votes, key=votes.get)
        total  = sum(votes.values())
        conf   = votes[action] / total if total > 0 else 0

        return EnsembleSignal(
            date=datetime.now().strftime("%Y%m%d"),
            action=action,
            confidence=conf,
            contributions=contributions,
            reason=f"加权投票: BUY={votes['BUY']:.2f}, SELL={votes['SELL']:.2f}, HOLD={votes['HOLD']:.2f}",
        )

    def update_performance(self, name: str, return_pct: float):
        self.weight_allocator.update_performance(name, return_pct)

    def save(self):
        self.library.save()

    def load(self):
        self.library.load()

__all__ = [
    "StrategyEvolutionOptimizer",
    "FrozenStrategy",
    "EnsembleSignal",
    "StrategyLibrary",
    "DynamicWeightAllocator",
    "StrategyEnsemble",
]
