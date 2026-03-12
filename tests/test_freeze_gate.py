from app.freeze_gate import build_freeze_gate_steps


def test_build_freeze_gate_steps_quick_contains_contract_and_focused_suite():
    steps = build_freeze_gate_steps(mode="quick")

    assert [step.name for step in steps] == [
        "contract-drift-check",
        "focused-protocol-regression",
    ]
    assert steps[0].command[-1] == "--check"
    assert "tests/test_runtime_contract_generation.py" in steps[1].command


def test_build_freeze_gate_steps_full_adds_full_regression_suite():
    steps = build_freeze_gate_steps(mode="full")

    assert [step.name for step in steps] == [
        "contract-drift-check",
        "focused-protocol-regression",
        "full-regression-suite",
    ]
    assert "tests/test_commander.py" in steps[-1].command
    assert "tests/test_runtime_api_contract.py" in steps[-1].command
