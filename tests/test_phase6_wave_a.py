from __future__ import annotations

from flask import Flask
import pandas as pd

from invest_evolution.interfaces.web import register_runtime_interface_routes
import invest_evolution.interfaces.web.server as web_server
from invest_evolution.investment.evolution import EvolutionService
from invest_evolution.market_data.manager import BenchmarkDataService, MarketQueryService


def test_web_interface_registry_registers_all_route_groups(monkeypatch):
    calls: list[str] = []

    def _fake_load_registrar(path: str):
        if "read" in path:
            return lambda app, **kwargs: calls.append("read")
        if "ops" in path:
            return lambda app, **kwargs: calls.append("ops")
        if "data" in path:
            return lambda app, **kwargs: calls.append("data")
        if "command" in path:
            return lambda app, **kwargs: calls.append("command")
        return lambda app, **kwargs: calls.append("contracts")

    monkeypatch.setattr(web_server, "_load_registrar", _fake_load_registrar)

    register_runtime_interface_routes(Flask(__name__))

    assert calls == ["read", "ops", "data", "command", "contracts"]


def test_market_data_service_exports_keep_runtime_boundaries_accessible():
    assert MarketQueryService.__name__ == "MarketQueryService"
    assert BenchmarkDataService.__name__ == "BenchmarkDataService"


def test_benchmark_service_facade_delegates_to_underlying_manager():
    class DummyManager:
        def get_benchmark_daily_values(
            self,
            trading_dates: list[str],
            index_code: str = "sh.000300",
        ) -> list[float]:
            del trading_dates, index_code
            return [1.0, 2.0]

        def get_market_index_frame(
            self,
            index_code: str = "sh.000300",
            start_date: str | None = None,
            end_date: str | None = None,
        ) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "index_code": index_code,
                        "start_date": start_date,
                        "end_date": end_date,
                    }
                ]
            )

    manager = DummyManager()
    benchmark = BenchmarkDataService(data_manager=manager)

    assert benchmark.get_benchmark_daily_values(["20240101"]) == [1.0, 2.0]
    frame = benchmark.get_market_index_frame(index_code="sh.000300")
    assert frame.iloc[0].to_dict() == {
        "index_code": "sh.000300",
        "start_date": None,
        "end_date": None,
    }


def test_evolution_service_facade_delegates():
    class DummyEngine:
        def __init__(self):
            self.initialized = None

        def initialize_population(self, base_params=None):
            self.initialized = base_params

        def evolve(self, fitness_scores):
            return [{"scores": fitness_scores}]

        def get_best_params(self):
            return {"position_size": 0.2}

    evolution = EvolutionService(engine=DummyEngine())

    evolution.initialize_population({"position_size": 0.1})
    assert evolution.evolve([1.0]) == [{"scores": [1.0]}]
    assert evolution.get_best_params() == {"position_size": 0.2}


def test_evolution_service_does_not_swallow_type_error_from_engine():
    class DummyEngine:
        def initialize_population(self, base_params=None):
            raise TypeError("engine bug")

        def evolve(self, fitness_scores):
            return fitness_scores

        def get_best_params(self):
            return {}

    evolution = EvolutionService(engine=DummyEngine())

    import pytest

    with pytest.raises(TypeError, match="engine bug"):
        evolution.initialize_population({"position_size": 0.1})
