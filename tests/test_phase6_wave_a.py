from __future__ import annotations

from flask import Flask
import pandas as pd

from app.application import (
    CommanderRuntimeFacade,
    EvolutionService,
    ReviewMeetingService,
    SelectionMeetingService,
    StockAnalysisOrchestrator,
    TrainingOrchestrator,
)
from app.commander import CommanderRuntime
from app.interfaces.web import register_runtime_interface_routes
from app.stock_analysis import StockAnalysisService
from app.train import SelfLearningController
from market_data.services import (
    BenchmarkDataService,
    DataAvailabilityService,
    QualityAuditService,
    TrainingDatasetResolver,
)


def test_phase6_application_facades_preserve_legacy_runtime_types():
    assert issubclass(CommanderRuntimeFacade, CommanderRuntime)
    assert issubclass(TrainingOrchestrator, SelfLearningController)
    assert issubclass(StockAnalysisOrchestrator, StockAnalysisService)


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


def test_market_data_service_facades_delegate_to_underlying_manager():
    class DummyManager:
        def check_training_readiness(
            self,
            cutoff_date: str,
            stock_count: int = 50,
            min_history_days: int = 200,
        ) -> dict[str, object]:
            return {
                "kind": "check",
                "cutoff_date": cutoff_date,
                "stock_count": stock_count,
                "min_history_days": min_history_days,
            }

        def diagnose_training_data(
            self,
            cutoff_date: str,
            stock_count: int = 50,
            min_history_days: int = 200,
        ) -> dict[str, object]:
            return {
                "kind": "diagnose",
                "cutoff_date": cutoff_date,
                "stock_count": stock_count,
                "min_history_days": min_history_days,
            }

        def get_status_summary(self, *, refresh: bool = False) -> dict[str, object]:
            return {"refresh": refresh}

        def random_cutoff_date(
            self,
            min_date: str = "20180101",
            max_date: str | None = None,
        ) -> str:
            del min_date, max_date
            return "20240101"

        def load_stock_data(
            self,
            cutoff_date: str,
            stock_count: int = 50,
            min_history_days: int = 200,
            include_future_days: int = 0,
            include_capital_flow: bool = False,
        ) -> dict[str, pd.DataFrame]:
            return {
                "sh.600519": pd.DataFrame(
                    [
                        {
                            "cutoff_date": cutoff_date,
                            "stock_count": stock_count,
                            "min_history_days": min_history_days,
                            "include_future_days": include_future_days,
                            "include_capital_flow": include_capital_flow,
                        }
                    ]
                )
            }

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
    availability = DataAvailabilityService(data_manager=manager)
    resolver = TrainingDatasetResolver(data_manager=manager)
    benchmark = BenchmarkDataService(data_manager=manager)

    assert availability.check_training_readiness(cutoff_date="20240101")["kind"] == "check"
    assert availability.diagnose_training_data(cutoff_date="20240101")["kind"] == "diagnose"
    assert availability.get_status_summary(refresh=True) == {"refresh": True}
    assert resolver.random_cutoff_date() == "20240101"
    loaded = resolver.load_stock_data(cutoff_date="20240101")
    assert list(loaded) == ["sh.600519"]
    assert loaded["sh.600519"].iloc[0]["cutoff_date"] == "20240101"
    assert benchmark.get_benchmark_daily_values(["20240101"]) == [1.0, 2.0]
    frame = benchmark.get_market_index_frame(index_code="sh.000300")
    assert frame.iloc[0].to_dict() == {
        "index_code": "sh.000300",
        "start_date": None,
        "end_date": None,
    }


def test_quality_service_facade_delegates():
    class DummyQualityService:
        def audit(self, **kwargs):
            return {"mode": "audit", **kwargs}

        def persist_audit(self, *, force_refresh: bool = True):
            return {"mode": "persist", "force_refresh": force_refresh}

    service = QualityAuditService(quality_service=DummyQualityService())
    assert service.audit(force_refresh=True)["mode"] == "audit"
    assert service.persist_audit(force_refresh=False) == {
        "mode": "persist",
        "force_refresh": False,
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
