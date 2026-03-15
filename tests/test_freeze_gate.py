from app.freeze_gate import build_freeze_gate_steps


def test_build_freeze_gate_steps_quick_contains_contract_and_focused_suite():
    steps = build_freeze_gate_steps(mode="quick")

    assert [step.name for step in steps] == [
        "contract-drift-check",
        "focused-protocol-regression",
        "critical-ruff-check",
        "critical-pyright-check",
    ]
    assert steps[0].command[-1] == "--check"
    assert "tests/test_runtime_contract_generation.py" in steps[1].command
    assert "tests/test_structured_output_adapter.py" in steps[1].command
    assert "tests/test_v2_contracts.py" in steps[1].command
    assert "tests/test_web_training_lab_api.py" in steps[1].command
    assert "tests/test_training_promotion_lineage.py" in steps[1].command
    assert "brain/guardrails.py" in steps[2].command
    assert "brain/structured_output.py" in steps[2].command
    assert "market_data/quality.py" in steps[2].command
    assert "app/commander_support/status.py" in steps[3].command
    assert "invest/contracts/agent_context.py" in steps[3].command
    assert "app/commander.py" in steps[3].command


def test_build_freeze_gate_steps_full_adds_full_regression_suite():
    steps = build_freeze_gate_steps(mode="full")

    assert [step.name for step in steps] == [
        "contract-drift-check",
        "focused-protocol-regression",
        "critical-ruff-check",
        "critical-pyright-check",
        "full-regression-suite",
    ]
    assert "tests/test_commander.py" in steps[-1].command
    assert "tests/test_runtime_api_contract.py" in steps[-1].command
