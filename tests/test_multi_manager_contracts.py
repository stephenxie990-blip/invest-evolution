from invest_evolution.investment.contracts import (
    AllocationReviewReport,
    ManagerPlan,
    ManagerPlanPosition,
    ManagerResult,
    ManagerReviewReport,
    ManagerRunContext,
    ManagerSpec,
    PortfolioPlan,
    PortfolioPlanPosition,
)


def test_multi_manager_contracts_round_trip():
    spec = ManagerSpec(
        manager_id="momentum",
        runtime_id="momentum",
        display_name="Momentum Manager",
        capability_allowlist=["screening", "screening", "plan_assembly"],
    )
    context = ManagerRunContext(
        as_of_date="20260318",
        regime="bull",
        budget_weights={"momentum": 0.6, "value_quality": 0.4},
        active_manager_ids=["momentum", "value_quality"],
    )
    plan = ManagerPlan(
        manager_id="momentum",
        manager_name="Momentum Manager",
        as_of_date="20260318",
        regime="bull",
        positions=[
            ManagerPlanPosition(
                code="sh.600000",
                rank=1,
                target_weight=0.5,
                thesis="trend persistence",
            )
        ],
        cash_reserve=0.5,
        max_positions=1,
        budget_weight=0.6,
        confidence=0.82,
        reasoning="bull market continuation",
        source_manager_id="momentum",
        source_manager_config_ref="momentum_v1",
    )
    result = ManagerResult(
        manager_id="momentum",
        as_of_date="20260318",
        status="planned",
        plan=plan,
    )
    portfolio = PortfolioPlan(
        as_of_date="20260318",
        regime="bull",
        positions=[
            PortfolioPlanPosition(
                code="sh.600000",
                target_weight=0.3,
                source_managers=["momentum"],
                manager_weights={"momentum": 0.3},
                thesis="trend persistence",
            )
        ],
        cash_reserve=0.7,
        active_manager_ids=["momentum"],
        manager_weights={"momentum": 1.0},
        confidence=0.82,
        reasoning="assembled 1 holdings from 1 manager plans",
    )
    manager_review = ManagerReviewReport(
        manager_id="momentum",
        as_of_date="20260318",
        verdict="hold",
        findings=["no issue"],
    )
    allocation_review = AllocationReviewReport(
        as_of_date="20260318",
        regime="bull",
        verdict="hold",
        active_manager_ids=["momentum"],
    )

    assert spec.to_dict()["display_name"] == "Momentum Manager"
    assert spec.capability_allowlist == ["screening", "plan_assembly"]
    assert context.to_dict()["budget_weights"]["momentum"] == 0.6
    assert plan.to_trading_plan().stock_codes == ["sh.600000"]
    assert result.to_dict()["plan"]["manager_id"] == "momentum"
    assert portfolio.to_trading_plan().stock_codes == ["sh.600000"]
    assert manager_review.to_dict()["verdict"] == "hold"
    assert allocation_review.to_dict()["regime"] == "bull"
