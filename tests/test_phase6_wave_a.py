from __future__ import annotations

from flask import Flask
import pandas as pd

from app.interfaces.web import register_runtime_interface_routes
from invest.services import EvolutionService, ReviewMeetingService, SelectionMeetingService
from market_data.services import BenchmarkDataService, MarketQueryService


def test_phase6_service_exports_keep_runtime_boundaries_accessible():
    assert SelectionMeetingService.__name__ == "SelectionMeetingService"
    assert ReviewMeetingService.__name__ == "ReviewMeetingService"
    assert EvolutionService.__name__ == "EvolutionService"


def test_web_interface_registry_registers_all_route_groups(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.interfaces.web.registry.register_runtime_read_routes",
        lambda app, **kwargs: calls.append("read"),
    )
    monkeypatch.setattr(
        "app.interfaces.web.registry.register_runtime_ops_routes",
        lambda app, **kwargs: calls.append("ops"),
    )
    monkeypatch.setattr(
        "app.interfaces.web.registry.register_runtime_data_routes",
        lambda app, **kwargs: calls.append("data"),
    )
    monkeypatch.setattr(
        "app.interfaces.web.registry.register_runtime_command_routes",
        lambda app, **kwargs: calls.append("command"),
    )
    monkeypatch.setattr(
        "app.interfaces.web.registry.register_runtime_contract_routes",
        lambda app, **kwargs: calls.append("contracts"),
    )

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


def test_invest_service_facades_delegate():
    class DummySelectionMeeting:
        def run(self, regime, stock_summaries, top_n=5):
            return {"regime": regime, "top_n": top_n, "count": len(stock_summaries)}

        def run_with_model_output(self, model_output):
            return {"model_output": model_output}

        def run_with_context(self, signal_packet, agent_context):
            return {"signal_packet": signal_packet, "agent_context": agent_context}

        def update_weights(self, weight_adjustments):
            self.last_weights = weight_adjustments

    class DummyReviewMeeting:
        def run_with_eval_report(self, eval_report, **kwargs):
            return {"eval_report": eval_report, **kwargs}

        def set_policy(self, policy=None):
            self.policy = policy

    class DummyEngine:
        def __init__(self):
            self.initialized = None

        def initialize_population(self, base_params=None):
            self.initialized = base_params

        def evolve(self, fitness_scores):
            return [{"scores": fitness_scores}]

        def get_best_params(self):
            return {"position_size": 0.2}

    selection = SelectionMeetingService(meeting=DummySelectionMeeting())
    review = ReviewMeetingService(meeting=DummyReviewMeeting())
    evolution = EvolutionService(engine=DummyEngine())

    assert selection.run({"regime": "bull"}, [{"code": "sh.600519"}], top_n=1) == {
        "regime": {"regime": "bull"},
        "top_n": 1,
        "count": 1,
    }
    assert review.run_with_eval_report({"return_pct": 3.0}, agent_accuracy={}, current_params={})["eval_report"] == {"return_pct": 3.0}
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
