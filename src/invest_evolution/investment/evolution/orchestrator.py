import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .analysis import LLMOptimizer
from invest_evolution.investment.shared.llm import LLMCaller

logger = logging.getLogger(__name__)


class RuntimeEvolutionOptimizer:
    """
    Runtime 进化优化器（遗传算法 + LLM）

    亏损周期 → LLM 分析 → 参数调整
    盈利周期 → 近期收益 > 5%时略微激进，否则保守
    """

    def __init__(
        self,
        *,
        llm_optimizer: LLMOptimizer | None = None,
        llm_caller: LLMCaller | None = None,
    ):
        self.llm_optimizer = llm_optimizer or LLMOptimizer(llm_caller=llm_caller)
        self.best_params: dict[str, Any] = {}
        self.generation = 0

    def optimize(
        self,
        cycle_results: List[Dict[str, Any]],
        current_params: dict[str, Any],
    ) -> dict[str, Any]:
        logger.info(f"runtime 优化第 {self.generation + 1} 代")
        self.generation += 1

        if cycle_results:
            latest = cycle_results[-1]
            if latest.get("return_pct", 0) < 0:
                analysis = self.llm_optimizer.analyze_loss(
                    latest, latest.get("trade_history", [])
                )
                new_params = dict(self.llm_optimizer.generate_runtime_fix(analysis))
                logger.info(f"LLM 参数调整: {new_params}")
                self.best_params = dict(new_params)
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
# Part 4: Runtime 库 + 集成层
# ============================================================

@dataclass
class FrozenManagerRuntime:
    """固化 manager runtime"""
    name: str
    params: Dict[str, float]
    performance: Dict[str, float]
    frozen_date: str
    win_rate: float = 0.0
    avg_return: float = 0.0


@dataclass
class RuntimeEnsembleSignal:
    """runtime 集成信号"""
    date: str
    action: str          # BUY / SELL / HOLD
    confidence: float
    contributions: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


class RuntimeLibrary:
    """runtime 库（JSON 持久化）"""

    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = Path(storage_path) if storage_path else (
            Path.home() / ".invest_ai" / "runtimes"
        )
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.runtimes: Dict[str, FrozenManagerRuntime] = {}

    def add_runtime(self, runtime: FrozenManagerRuntime):
        self.runtimes[runtime.name] = runtime
        logger.info(f"runtime 库添加: {runtime.name}")

    def get_runtime(self, name: str) -> Optional[FrozenManagerRuntime]:
        return self.runtimes.get(name)

    def get_all_runtimes(self) -> List[FrozenManagerRuntime]:
        return list(self.runtimes.values())

    def save(self):
        path = self.storage_path / "runtime_library.json"
        data = {
            name: {
                "params": runtime.params, "performance": runtime.performance,
                "frozen_date": runtime.frozen_date, "win_rate": runtime.win_rate,
                "avg_return": runtime.avg_return,
            }
            for name, runtime in self.runtimes.items()
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"runtime 库已保存: {path}")

    def load(self):
        path = self.storage_path / "runtime_library.json"
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for name, runtime in data.items():
            self.runtimes[name] = FrozenManagerRuntime(
                name=name, params=runtime["params"], performance=runtime.get("performance", {}),
                frozen_date=runtime.get("frozen_date", ""), win_rate=runtime.get("win_rate", 0),
                avg_return=runtime.get("avg_return", 0),
            )
        logger.info(f"runtime 库已加载: {len(self.runtimes)} 个 runtime")


class RuntimeWeightAllocator:
    """
    动态权重分配器

    近期表现好的 runtime 权重高（线性加权平均）
    单 runtime 权重上限 50%
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

    def calculate_weights(self, runtimes: List[FrozenManagerRuntime]) -> Dict[str, float]:
        if not runtimes:
            return {}

        raw = {}
        for runtime in runtimes:
            hist = self.performance_history.get(runtime.name, [])
            if hist:
                w = np.linspace(1, 2, len(hist))
                score = float(np.average(hist, weights=w))
            else:
                score = runtime.win_rate
            raw[runtime.name] = max(score, 0)

        total = sum(raw.values())
        norm  = {k: v / total for k, v in raw.items()} if total > 0 else {
            k: 1.0 / len(runtimes) for k in raw
        }

        capped = {k: min(v, self.max_weight) for k, v in norm.items()}
        total  = sum(capped.values())
        final  = {k: v / total for k, v in capped.items()} if total > 0 else capped

        self.current_weights = final
        logger.info(f"动态权重: {final}")
        return final


class RuntimeEnsemble:
    """
    多 runtime 集成层（加权投票）

    runtime_signals: {runtime 名: "BUY"/"SELL"/"HOLD"}
    加权投票 → 最终信号 + 置信度
    """

    def __init__(self):
        self.library         = RuntimeLibrary()
        self.weight_allocator = RuntimeWeightAllocator()

    def add_runtime(self, name: str, params: Dict, performance: Dict):
        self.library.add_runtime(FrozenManagerRuntime(
            name=name, params=params, performance=performance,
            frozen_date=datetime.now().strftime("%Y%m%d"),
            win_rate=performance.get("win_rate", 0),
            avg_return=performance.get("avg_return", 0),
        ))

    def generate_signal(self, runtime_signals: Dict[str, str]) -> RuntimeEnsembleSignal:
        runtimes = self.library.get_all_runtimes()
        if not runtimes:
            return RuntimeEnsembleSignal(date=datetime.now().strftime("%Y%m%d"), action="HOLD", confidence=0, reason="runtime 库为空")

        weights = self.weight_allocator.calculate_weights(runtimes)
        votes: Dict[str, float] = {"BUY": 0, "SELL": 0, "HOLD": 0}
        contributions = {}

        for name, sig in runtime_signals.items():
            w = weights.get(name, 0)
            votes[sig] = votes.get(sig, 0) + w
            contributions[name] = {"signal": sig, "weight": w}

        action = max(votes, key=lambda key: votes[key])
        total  = sum(votes.values())
        conf   = votes[action] / total if total > 0 else 0

        return RuntimeEnsembleSignal(
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
    "RuntimeEvolutionOptimizer",
    "FrozenManagerRuntime",
    "RuntimeEnsembleSignal",
    "RuntimeLibrary",
    "RuntimeWeightAllocator",
    "RuntimeEnsemble",
]
