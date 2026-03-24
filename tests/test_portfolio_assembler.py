from invest_evolution.investment.contracts import ManagerPlan, ManagerPlanPosition
from invest_evolution.investment.governance import PortfolioAssembler


def test_portfolio_assembler_merges_duplicate_holdings():
    assembler = PortfolioAssembler()
    plan_a = ManagerPlan(
        manager_id="momentum",
        manager_name="Momentum Manager",
        as_of_date="20260318",
        regime="bull",
        positions=[
            ManagerPlanPosition(code="AAA", rank=1, target_weight=0.6, thesis="trend"),
            ManagerPlanPosition(code="BBB", rank=2, target_weight=0.2, thesis="follow through"),
        ],
        cash_reserve=0.2,
        max_positions=2,
        budget_weight=0.6,
        confidence=0.8,
    )
    plan_b = ManagerPlan(
        manager_id="value_quality",
        manager_name="Value Quality Manager",
        as_of_date="20260318",
        regime="bull",
        positions=[
            ManagerPlanPosition(code="AAA", rank=1, target_weight=0.4, thesis="cheap quality"),
            ManagerPlanPosition(code="CCC", rank=2, target_weight=0.4, thesis="balance sheet"),
        ],
        cash_reserve=0.2,
        max_positions=2,
        budget_weight=0.4,
        confidence=0.7,
    )

    portfolio = assembler.assemble([plan_a, plan_b], manager_weights={"momentum": 0.6, "value_quality": 0.4})

    assert portfolio.active_manager_ids == ["momentum", "value_quality"]
    assert portfolio.positions[0].code == "AAA"
    assert set(portfolio.positions[0].source_managers) == {"momentum", "value_quality"}
    assert portfolio.metadata["duplicate_holdings"] >= 1
    assert abs(sum(position.target_weight for position in portfolio.positions) - (1.0 - portfolio.cash_reserve)) < 1e-6


def test_portfolio_assembler_default_caps_portfolio_width_to_eight_holdings():
    assembler = PortfolioAssembler()
    plan_a = ManagerPlan(
        manager_id="momentum",
        manager_name="Momentum Manager",
        as_of_date="20260318",
        regime="bull",
        positions=[
            ManagerPlanPosition(code=f"A{i}", rank=i, target_weight=0.12, thesis="trend")
            for i in range(1, 7)
        ],
        cash_reserve=0.2,
        max_positions=6,
        budget_weight=0.5,
        confidence=0.8,
    )
    plan_b = ManagerPlan(
        manager_id="value_quality",
        manager_name="Value Quality Manager",
        as_of_date="20260318",
        regime="bull",
        positions=[
            ManagerPlanPosition(code=f"B{i}", rank=i, target_weight=0.12, thesis="quality")
            for i in range(1, 7)
        ],
        cash_reserve=0.2,
        max_positions=6,
        budget_weight=0.5,
        confidence=0.7,
    )

    portfolio = assembler.assemble([plan_a, plan_b], manager_weights={"momentum": 0.5, "value_quality": 0.5})

    assert assembler.config.max_positions == 8
    assert len(portfolio.positions) == 8
