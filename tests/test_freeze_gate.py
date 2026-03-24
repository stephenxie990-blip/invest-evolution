from invest_evolution.application.freeze_gate import (
    CRITICAL_PYRIGHT_TARGETS,
    CRITICAL_RUFF_TARGETS,
    FOCUSED_PROTOCOL_TESTS,
    FULL_REGRESSION_TESTS,
    PROJECT_ROOT,
    build_freeze_gate_steps,
)


def test_build_freeze_gate_steps_quick_contains_contract_and_focused_suite():
    steps = build_freeze_gate_steps(mode="quick")

    assert [step.name for step in steps] == [
        "contract-drift-check",
        "focused-protocol-regression",
        "critical-ruff-check",
        "critical-pyright-check",
    ]
    assert steps[0].command[-1] == "--check"
    assert "uv" not in steps[1].command
    assert steps[1].command[:3] == ["python", "-m", "pytest"] or steps[1].command[1:3] == ["-m", "pytest"]
    assert "tests/test_runtime_api_contract.py" in steps[1].command
    assert "tests/test_structured_output_adapter.py" in steps[1].command
    assert "tests/test_v2_contracts.py" in steps[1].command
    assert "tests/test_web_training_lab_api.py" in steps[1].command
    assert "tests/test_training_promotion_lineage.py" in steps[1].command
    assert steps[2].command[:3] == ["python", "-m", "ruff"] or steps[2].command[1:3] == ["-m", "ruff"]
    assert "src/invest_evolution/agent_runtime/runtime.py" in steps[2].command
    assert "src/invest_evolution/agent_runtime/presentation.py" in steps[2].command
    assert "src/invest_evolution/market_data/manager.py" in steps[2].command
    assert steps[3].command[:3] == ["python", "-m", "pyright"] or steps[3].command[1:3] == ["-m", "pyright"]
    assert "src/invest_evolution/application/commander/status.py" in steps[3].command
    assert "src/invest_evolution/investment/contracts/core.py" in steps[3].command
    assert "src/invest_evolution/application/commander_main.py" in steps[3].command


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


def test_freeze_gate_targets_exist_in_repo():
    for rel_path in [
        *CRITICAL_RUFF_TARGETS,
        *CRITICAL_PYRIGHT_TARGETS,
        *FOCUSED_PROTOCOL_TESTS,
        *FULL_REGRESSION_TESTS,
        "scripts/generate_runtime_contract_derivatives.py",
    ]:
        assert (PROJECT_ROOT / rel_path).exists(), f"missing freeze-gate path: {rel_path}"

def test_force_full_cycles_disables_early_freeze(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("pandas")
    import invest_evolution.config as config_module
    from invest_evolution.application.train import SelfLearningController
    monkeypatch.setattr(config_module.config, "stop_on_freeze", False, raising=False)
    controller = SelfLearningController(
        output_dir=str(tmp_path / "out"),
        artifact_log_dir=str(tmp_path / "artifacts"),
        config_audit_log_path=str(tmp_path / "state" / "audit.jsonl"),
        config_snapshot_dir=str(tmp_path / "state" / "snapshots"),
    )
    assert controller.stop_on_freeze is False
