from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONTRACT_CHECK_CMD = [sys.executable, 'scripts/generate_frontend_contract_derivatives.py', '--check']

FOCUSED_PROTOCOL_TESTS = [
    'tests/test_schema_contracts.py',
    'tests/test_commander_transcript_golden.py',
    'tests/test_commander_mutating_workflow_golden.py',
    'tests/test_commander_direct_planner_golden.py',
    'tests/test_frontend_api_contract.py',
    'tests/test_frontend_contract_generation.py',
]

FULL_REGRESSION_TESTS = [
    'tests/test_stock_analysis_react.py',
    'tests/test_ask_stock_model_bridge.py',
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
    'tests/test_frontend_api_contract.py',
    'tests/test_frontend_contract_generation.py',
]


@dataclass(frozen=True)
class FreezeGateStep:
    name: str
    command: list[str]


def build_freeze_gate_steps(*, mode: str = 'full') -> list[FreezeGateStep]:
    normalized_mode = str(mode or 'full').strip().lower()
    if normalized_mode not in {'quick', 'full'}:
        raise ValueError(f'unsupported freeze gate mode: {mode}')
    steps = [
        FreezeGateStep(name='contract-drift-check', command=CONTRACT_CHECK_CMD),
        FreezeGateStep(name='focused-protocol-regression', command=['uv', 'run', 'pytest', '-q', *FOCUSED_PROTOCOL_TESTS]),
    ]
    if normalized_mode == 'full':
        steps.append(FreezeGateStep(name='full-regression-suite', command=['uv', 'run', 'pytest', '-q', *FULL_REGRESSION_TESTS]))
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
