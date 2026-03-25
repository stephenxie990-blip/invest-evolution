from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from invest_evolution.application.verification_targets import (
    CONTRACT_CHECK_CMD,
    CRITICAL_PYRIGHT_TARGETS,
    CRITICAL_RUFF_TARGETS,
    focused_protocol_tests,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FOCUSED_PROTOCOL_TESTS = tuple(focused_protocol_tests(include_research=True))

FULL_REGRESSION_TESTS = [
    'tests/test_stock_analysis_react.py',
    'tests/test_ask_stock_manager_bridge.py',
    'tests/test_commander.py',
    'tests/test_schema_contracts.py',
    'tests/test_commander_unified_entry.py',
    'tests/test_commander_transcript_golden.py',
    'tests/test_commander_mutating_workflow_golden.py',
    'tests/test_commander_direct_planner_golden.py',
    'tests/test_brain_runtime.py',
    'tests/test_web_server_runtime_and_bool.py',
    'tests/test_web_server_contract_headers.py',
    'tests/test_web_training_lab_api.py',
    'tests/test_web_server_data_api.py',
    'tests/test_data_unification.py',
    'tests/test_runtime_api_contract.py',
]


@dataclass(frozen=True)
class FreezeGateStep:
    name: str
    command: list[str]


def _assert_relative_paths_exist(paths: Sequence[str]) -> None:
    missing = [path for path in paths if not (PROJECT_ROOT / path).exists()]
    if missing:
        rendered = ', '.join(missing)
        raise ValueError(f'freeze gate references missing repository paths: {rendered}')


def _uv_tool_cmd(tool: str, *args: str) -> list[str]:
    return ['uv', 'run', tool, *args]


def build_freeze_gate_steps(*, mode: str = 'full') -> list[FreezeGateStep]:
    normalized_mode = str(mode or 'full').strip().lower()
    if normalized_mode not in {'quick', 'full'}:
        raise ValueError(f'unsupported freeze gate mode: {mode}')
    _assert_relative_paths_exist(
        [
            'scripts/generate_runtime_contract_derivatives.py',
            *CRITICAL_RUFF_TARGETS,
            *CRITICAL_PYRIGHT_TARGETS,
            *FOCUSED_PROTOCOL_TESTS,
            *FULL_REGRESSION_TESTS,
        ]
    )
    steps = [
        FreezeGateStep(name='contract-drift-check', command=list(CONTRACT_CHECK_CMD)),
        FreezeGateStep(
            name='focused-protocol-regression',
            command=_uv_tool_cmd(
                'pytest',
                '-q',
                *FOCUSED_PROTOCOL_TESTS,
            ),
        ),
        FreezeGateStep(name='critical-ruff-check', command=_uv_tool_cmd('ruff', 'check', *CRITICAL_RUFF_TARGETS)),
        FreezeGateStep(name='critical-pyright-check', command=_uv_tool_cmd('pyright', *CRITICAL_PYRIGHT_TARGETS)),
    ]
    if normalized_mode == 'full':
        steps.append(FreezeGateStep(name='full-regression-suite', command=_uv_tool_cmd('pytest', '-q', *FULL_REGRESSION_TESTS)))
    return steps


def run_freeze_gate(*, mode: str = 'full') -> int:
    steps = build_freeze_gate_steps(mode=mode)
    for step in steps:
        print(f'==> {step.name}')
        result = subprocess.run(step.command, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f'freeze gate failed at step: {step.name}', file=sys.stderr)
            return result.returncode or 1
    print('freeze gate passed')
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run contract freeze and regression gates.')
    parser.add_argument('--mode', choices=['quick', 'full'], default='full', help='quick=contract+focused suite; full=quick+full regression')
    parser.add_argument('--list', action='store_true', help='Print the planned commands without executing them.')
    args = parser.parse_args(list(argv) if argv is not None else None)

    steps = build_freeze_gate_steps(mode=args.mode)
    if args.list:
        for step in steps:
            print(f"{step.name}: {' '.join(step.command)}")
        return 0
    return run_freeze_gate(mode=args.mode)


if __name__ == '__main__':
    raise SystemExit(main())
