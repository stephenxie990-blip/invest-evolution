from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from invest_evolution.application.verification_targets import (
    CRITICAL_PYRIGHT_TARGETS,
    CRITICAL_RUFF_TARGETS,
    focused_protocol_tests,
)

REPORT_PATH = Path("reports") / "verification_report.json"


@dataclass
class GateResult:
    name: str
    command: Sequence[str]
    returncode: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout.strip(),
            "stderr": self.stderr.strip(),
        }


def run_gate(name: str, command: Sequence[str]) -> GateResult:
    process = subprocess.run(
        command, cwd=Path.cwd(), capture_output=True, text=True
    )
    return GateResult(
        name=name,
        command=tuple(command),
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def generate_commands() -> list[tuple[str, Sequence[str]]]:
    commands: list[tuple[str, Sequence[str]]] = [
        (
            "freeze_gate_quick",
            ("uv", "run", "invest-freeze-gate", "--mode", "quick"),
        ),
        ("critical_pyright", ("uv", "run", "pyright", *CRITICAL_PYRIGHT_TARGETS)),
        ("critical_ruff", ("uv", "run", "ruff", "check", *CRITICAL_RUFF_TARGETS)),
        (
            "focused_protocol_tests",
            ("uv", "run", "pytest", "-q", *focused_protocol_tests(include_research=True)),
        ),
    ]
    return commands


def build_report(
    runner: Callable[[str, Sequence[str]], GateResult] | None = None,
) -> dict[str, object]:
    runner = runner or run_gate
    results = [runner(name, command) for name, command in generate_commands()]
    git_sha = (
        subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        .stdout.strip()
        or "unknown"
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "results": [result.to_dict() for result in results],
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
