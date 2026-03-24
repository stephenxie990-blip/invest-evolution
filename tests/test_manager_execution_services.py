from dataclasses import dataclass
from typing import Any, Dict, cast

from invest_evolution.application.training.execution import ManagerExecutionService
from invest_evolution.investment.contracts import (
    AgentContext,
    ManagerSpec,
    ManagerOutput,
    SignalPacket,
    SignalPacketContext,
    StockSignal,
)
from invest_evolution.investment.managers import ManagerRegistry
from invest_evolution.investment.managers.registry import RuntimeBackedManager


@dataclass
class FakeRuntime:
    manager_id: str

    def update_runtime_overrides(self, params: Dict[str, Any]) -> None:
        self.params = dict(params or {})

    def process(self, stock_data: Dict[str, Any], cutoff_date: str) -> ManagerOutput:
        del stock_data
        packet = SignalPacket(
            as_of_date=cutoff_date,
            manager_id=self.manager_id,
            manager_config_ref=f"{self.manager_id}_v1",
            regime="bull",
            signals=[
                StockSignal(code=f"{self.manager_id[:3]}_A", score=0.9, rank=1, evidence=[f"{self.manager_id} leader"]),
                StockSignal(code="SHARED", score=0.8, rank=2, evidence=[f"{self.manager_id} overlap"]),
            ],
            selected_codes=[f"{self.manager_id[:3]}_A", "SHARED"],
            max_positions=2,
            cash_reserve=0.2,
            params={"stop_loss_pct": 0.05, "take_profit_pct": 0.12},
            reasoning=f"{self.manager_id} reasoning",
            context=SignalPacketContext(market_stats={"market_breadth": 0.64}),
        )
        context = AgentContext(
            as_of_date=cutoff_date,
            manager_id=self.manager_id,
            manager_config_ref=f"{self.manager_id}_v1",
            summary=f"{self.manager_id} summary",
            narrative=f"{self.manager_id} narrative",
            regime="bull",
            confidence=0.78,
        )
        return ManagerOutput(
            manager_id=self.manager_id,
            manager_config_ref=f"{self.manager_id}_v1",
            signal_packet=packet,
            agent_context=context,
        )


class FakeRegistry(ManagerRegistry):
    def __init__(self) -> None:
        super().__init__(specs=[
            ManagerSpec(manager_id="momentum", runtime_id="momentum", display_name="Momentum Manager"),
            ManagerSpec(manager_id="value_quality", runtime_id="value_quality", display_name="Value Quality Manager"),
        ])

    def build_manager(
        self,
        manager_id: str,
        *,
        runtime_overrides: Dict[str, object] | None = None,
    ) -> RuntimeBackedManager:
        spec = self.resolve(manager_id)
        return RuntimeBackedManager(spec=spec, runtime=cast(Any, FakeRuntime(spec.manager_id)))


class DummyController:
    manager_active_ids = ["momentum", "value_quality"]
    manager_budget_weights = {"momentum": 0.7, "value_quality": 0.3}
    current_params = {"position_size": 0.15}
    last_governance_decision = {
        "regime": "bull",
        "evidence": {"market_observation": {"stats": {"market_breadth": 0.64}}},
    }


def test_manager_execution_service_builds_portfolio_bundle():
    service = ManagerExecutionService(registry=FakeRegistry())
    bundle = service.execute_manager_selection(
        DummyController(),
        cycle_id=1,
        cutoff_date="20260318",
        stock_data={},
    )

    assert bundle.run_context.regime == "bull"
    assert bundle.dominant_manager_id == "momentum"
    assert len(bundle.manager_results) == 2
    assert bundle.portfolio_plan.active_manager_ids == ["momentum", "value_quality"]
    assert "SHARED" in bundle.portfolio_plan.selected_codes
    assert bundle.execution_payload["trading_plan"]["source"] == "portfolio_assembler"
