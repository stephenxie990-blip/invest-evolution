from __future__ import annotations

import importlib
import random
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from invest_evolution.investment.evolution.analysis import (
    LLMOptimizer,
    TradeDetail,
    TradingAnalyzer,
)
from invest_evolution.investment.evolution.engine import EvolutionEngine
from invest_evolution.investment.evolution.optimization import (
    BayesianOptimizer,
    GaussianProcessModel,
    GeneticOptimizer,
    OptimizedParams,
    ThreeStageOptimizer,
)


def test_gaussian_process_model_predict_returns_defaults_without_training() -> None:
    model = GaussianProcessModel()

    mean, std = model.predict(np.array([[0.1], [0.5], [0.9]]))

    assert mean.tolist() == [0.0, 0.0, 0.0]
    assert std.tolist() == [1.0, 1.0, 1.0]


def test_bayesian_optimizer_norm_cdf_falls_back_without_scipy(monkeypatch: pytest.MonkeyPatch) -> None:
    optimizer = BayesianOptimizer({"x": (0.0, 1.0)}, n_iter=0)

    def _raise_import_error(name: str):
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", _raise_import_error)

    values = optimizer._norm_cdf(np.array([-1.0, 0.0, 1.0]))

    assert len(values) == 3
    assert values[0] < values[1] < values[2]
    assert values[1] == pytest.approx(0.5, rel=1e-6)


def test_bayesian_optimizer_tracks_best_history_with_seeded_sampling() -> None:
    random.seed(7)
    optimizer = BayesianOptimizer({"x": (-1.0, 1.0), "y": (-1.0, 1.0)}, n_iter=4)

    def fitness(params: dict[str, float]) -> float:
        return 1.0 - (params["x"] - 0.25) ** 2 - (params["y"] + 0.5) ** 2

    best_params, best_fitness, param_ranges = optimizer.optimize(fitness)

    assert len(optimizer.history) == 14
    assert best_fitness == max(score for _, score in optimizer.history)
    assert any(params == best_params for params, _ in optimizer.history)
    assert set(param_ranges) == {"x", "y"}
    assert -1.0 <= best_params["x"] <= 1.0
    assert -1.0 <= best_params["y"] <= 1.0


def test_genetic_optimizer_mutation_clamps_back_into_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    optimizer = GeneticOptimizer({"x": (0.0, 1.0)}, mutation_rate=1.0)
    values = iter([0.0, 10.0])

    monkeypatch.setattr(random, "random", lambda: next(values))
    monkeypatch.setattr(random, "gauss", lambda mu, sigma: 5.0)

    mutated = optimizer._mutate({"x": 0.9})

    assert mutated["x"] == 1.0


def test_three_stage_optimizer_prefers_better_stage_and_marks_conservative() -> None:
    optimizer = ThreeStageOptimizer({"x": (0.0, 1.0)}, bayesian_n_iter=1, ga_population=4, ga_generations=1)
    cast(Any, optimizer).bayesian = SimpleNamespace(
        optimize=lambda fitness_func: ({"x": 0.2}, 0.4, {"x": (0.1, 0.3)})
    )
    cast(Any, optimizer).genetic = SimpleNamespace(
        optimize=lambda fitness_func, param_ranges=None: ({"x": 0.8}, 0.9)
    )
    cast(Any, optimizer).robustness = SimpleNamespace(
        validate=lambda params, fitness_func, bounds: (0.35, False)
    )

    result = optimizer.optimize(lambda params: params["x"])

    assert isinstance(result, OptimizedParams)
    assert result.params == {"x": 0.8}
    assert result.fitness == 0.9
    assert result.stability_score == 0.35
    assert result.stage == "conservative"


def test_evolution_engine_aligns_short_fitness_scores_and_preserves_best_individual() -> None:
    engine = EvolutionEngine(population_size=4, elite_size=1, mutation_rate=0.0, crossover_rate=0.0)
    engine.initialize_population({"ma_short": 5, "ma_long": 20})
    engine.population[0].params = {"candidate": "a"}
    engine.population[1].params = {"candidate": "best"}
    engine.population[2].params = {"candidate": "c"}
    engine.population[3].params = {"candidate": "d"}

    new_population = engine.evolve([1.0, 3.0])

    assert len(new_population) == 4
    assert engine.generation == 1
    assert engine.best_individual is not None
    assert engine.best_individual.params == {"candidate": "best"}
    assert engine.best_individual.fitness == 3.0
    assert engine.get_best_params() == {"candidate": "best"}


def test_trading_analyzer_summarizes_industries_stop_losses_and_factors(monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = TradingAnalyzer()
    monkeypatch.setattr(
        analyzer,
        "get_industry",
        lambda code: {"sh.600000": "银行", "sz.000001": "科技"}.get(code, "其他"),
    )
    trades = [
        TradeDetail(
            date="20240102",
            code="sh.600000",
            action="SELL",
            price=10.0,
            shares=100,
            pnl=120.0,
            pnl_pct=12.0,
            reason="趋势突破",
        ),
        TradeDetail(
            date="20240103",
            code="sz.000001",
            action="SELL",
            price=8.0,
            shares=100,
            pnl=-80.0,
            pnl_pct=-8.0,
            reason="STOP_LOSS",
        ),
        TradeDetail(
            date="20240104",
            code="sz.000001",
            action="BUY",
            price=7.8,
            shares=100,
            pnl=0.0,
            pnl_pct=0.0,
            reason="趋势突破",
        ),
    ]

    summary = analyzer.analyze_trades(trades)
    factors = analyzer.build_factor_performance(trades)

    assert summary["total_trades"] == 3
    assert summary["sell_trades"] == 2
    assert summary["winning_trades"] == 1
    assert summary["losing_trades"] == 1
    assert summary["win_rate"] == 0.5
    assert summary["industry_stats"]["科技"]["count"] == 2
    assert summary["stop_loss_count"] == 1
    assert summary["avg_stop_loss"] == -8.0
    factor_map = {factor.factor_name: factor for factor in factors}
    assert factor_map["趋势突破"].selected_count == 2
    assert factor_map["STOP_LOSS"].win_rate == 0.0


def test_llm_optimizer_records_history_and_falls_back_when_llm_raises() -> None:
    optimizer = LLMOptimizer(
        llm_caller=cast(
            Any,
            SimpleNamespace(call=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))),
        )
    )

    result = optimizer.analyze_loss(
        {"cycle_id": 12, "cutoff_date": "20240301", "return_pct": -1.5, "total_trades": 4, "win_rate": 0.25},
        [{"date": "20240301", "action": "SELL", "ts_code": "sh.600000", "price": 10.0, "pnl": -50.0, "reason": "STOP"}],
    )

    assert "表现不佳" in result.cause
    assert optimizer.analysis_history[-1]["cycle_id"] == 12
    assert optimizer.generate_runtime_fix(
        cast(Any, SimpleNamespace(cause="none", runtime_adjustments={}))
    ) == {
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.10,
        "position_size": 0.15,
    }
