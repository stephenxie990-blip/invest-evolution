from __future__ import annotations

import sys
from typing import Sequence

CONTRACT_CHECK_CMD: tuple[str, ...] = (
    sys.executable,
    "scripts/generate_runtime_contract_derivatives.py",
    "--check",
)

CRITICAL_RUFF_TARGETS: tuple[str, ...] = (
    "src/invest_evolution/market_data/manager.py",
    "src/invest_evolution/market_data/repository.py",
    "src/invest_evolution/agent_runtime/memory.py",
    "src/invest_evolution/agent_runtime/runtime.py",
    "src/invest_evolution/agent_runtime/plugins.py",
    "src/invest_evolution/agent_runtime/presentation.py",
    "src/invest_evolution/investment/contracts/core.py",
    "src/invest_evolution/application/lab.py",
    "src/invest_evolution/application/commander/ops.py",
    "src/invest_evolution/application/commander/status.py",
    "src/invest_evolution/application/commander/presentation.py",
    "src/invest_evolution/interfaces/web/presentation.py",
    "src/invest_evolution/application/training/observability.py",
    "src/invest_evolution/application/training/execution.py",
)

CRITICAL_PYRIGHT_TARGETS: tuple[str, ...] = (
    "src/invest_evolution/market_data/manager.py",
    "src/invest_evolution/market_data/repository.py",
    "src/invest_evolution/agent_runtime/memory.py",
    "src/invest_evolution/agent_runtime/runtime.py",
    "src/invest_evolution/agent_runtime/plugins.py",
    "src/invest_evolution/agent_runtime/presentation.py",
    "src/invest_evolution/application/lab.py",
    "src/invest_evolution/application/commander_main.py",
    "src/invest_evolution/application/commander/presentation.py",
    "src/invest_evolution/application/commander/ops.py",
    "src/invest_evolution/application/commander/status.py",
    "src/invest_evolution/interfaces/web/presentation.py",
    "src/invest_evolution/application/training/observability.py",
    "src/invest_evolution/application/training/execution.py",
    "src/invest_evolution/investment/contracts/core.py",
)

FOCUSED_PROTOCOL_TESTS: tuple[str, ...] = (
    "tests/test_schema_contracts.py",
    "tests/test_architecture_closure_assets.py",
    "tests/test_commander_transcript_golden.py",
    "tests/test_commander_mutating_workflow_golden.py",
    "tests/test_commander_direct_planner_golden.py",
    "tests/test_runtime_api_contract.py",
    "tests/test_structure_guards.py",
    "tests/test_v2_contracts.py",
    "tests/test_structured_output_adapter.py",
    "tests/test_brain_runtime.py",
    "tests/test_lab_artifacts.py",
    "tests/test_governance_phase_a_f.py",
    "tests/test_web_training_lab_api.py",
    "tests/test_training_promotion_lineage.py",
    "tests/test_training_review_protocol.py",
)

RESEARCH_FEEDBACK_TESTS: tuple[str, ...] = (
    "tests/test_research_feedback_gate.py",
    "tests/test_research_case_store.py",
    "tests/test_research_training_feedback.py",
)

def focused_protocol_tests(include_research: bool = True) -> Sequence[str]:
    if include_research:
        return (*FOCUSED_PROTOCOL_TESTS, *RESEARCH_FEEDBACK_TESTS)
    return FOCUSED_PROTOCOL_TESTS

README_COMMANDS: tuple[str, ...] = (
    "uv run pyright",
    "uv run ruff check src/invest_evolution/market_data/manager.py src/invest_evolution/market_data/repository.py",
)
