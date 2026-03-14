"""Evolution service facade for Phase 6."""

from __future__ import annotations

from typing import Any, Protocol

from invest.evolution.engine import EvolutionEngine


class EvolutionEngineLike(Protocol):
    def initialize_population(self, base_params: dict[str, Any] | None = None) -> None:
        ...

    def evolve(self, fitness_scores: list[float]) -> list[Any]:
        ...

    def get_best_params(self) -> dict[str, Any]:
        ...


class EvolutionService:
    """Thin service wrapper over the existing evolution engine."""

    def __init__(self, engine: EvolutionEngineLike | None = None, **engine_kwargs: Any):
        self.engine = engine or EvolutionEngine(**engine_kwargs)

    def initialize_population(self, base_params: dict[str, Any] | None = None) -> None:
        try:
            self.engine.initialize_population(base_params=base_params)
        except TypeError:
            self.engine.initialize_population(base_params)

    def evolve(self, fitness_scores: list[float]) -> list[Any]:
        return self.engine.evolve(fitness_scores)

    def get_best_params(self) -> dict[str, Any]:
        return dict(self.engine.get_best_params() or {})

    @property
    def population_size(self) -> int:
        population = getattr(self.engine, "population", [])
        return len(population or [])
