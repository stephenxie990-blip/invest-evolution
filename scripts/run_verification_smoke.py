from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SmokeStep:
    name: str
    command: list[str]


def _python_module_cmd(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def build_smoke_steps() -> list[SmokeStep]:
    return [
        SmokeStep(
            name="env-bootstrap-check",
            command=[sys.executable, "scripts/bootstrap_env.py", "--check"],
        ),
        SmokeStep(
            name="focused-pytest",
            command=_python_module_cmd(
                "pytest",
                "-q",
                "tests/test_architecture_closure_assets.py",
                "tests/test_environment_bootstrap_assets.py",
                "tests/test_cli_entrypoint_smoke.py",
                "tests/test_deploy_public_surface_smoke.py",
                "tests/test_freeze_gate.py",
                "tests/test_runtime_service.py",
                "tests/test_deploy_topology_assets.py",
                "tests/test_gunicorn_conf.py",
                "tests/test_runtime_read_routes.py",
            ),
        ),
        SmokeStep(
            name="focused-ruff",
            command=_python_module_cmd(
                "ruff",
                "check",
                "src/invest_evolution/application/freeze_gate.py",
                "src/invest_evolution/application/runtime_service.py",
                "src/invest_evolution/interfaces/web/__init__.py",
                "src/invest_evolution/interfaces/web/runtime.py",
                "src/invest_evolution/interfaces/web/routes.py",
                "src/invest_evolution/interfaces/web/server.py",
                "scripts/bootstrap_env.py",
                "scripts/run_verification_smoke.py",
                "tests/test_architecture_closure_assets.py",
                "tests/test_environment_bootstrap_assets.py",
                "tests/test_cli_entrypoint_smoke.py",
                "tests/test_deploy_public_surface_smoke.py",
                "tests/test_freeze_gate.py",
                "tests/test_runtime_service.py",
                "tests/test_deploy_topology_assets.py",
                "tests/test_gunicorn_conf.py",
                "tests/test_runtime_read_routes.py",
            ),
        ),
        SmokeStep(
            name="focused-pyright",
            command=_python_module_cmd(
                "pyright",
                "src/invest_evolution/application/freeze_gate.py",
                "src/invest_evolution/application/runtime_service.py",
                "src/invest_evolution/interfaces/web/__init__.py",
                "src/invest_evolution/interfaces/web/runtime.py",
                "src/invest_evolution/interfaces/web/routes.py",
                "src/invest_evolution/interfaces/web/server.py",
                "scripts/bootstrap_env.py",
                "scripts/run_verification_smoke.py",
                "tests/test_architecture_closure_assets.py",
                "tests/test_environment_bootstrap_assets.py",
                "tests/test_cli_entrypoint_smoke.py",
                "tests/test_deploy_public_surface_smoke.py",
                "tests/test_runtime_read_routes.py",
            ),
        ),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    for step in build_smoke_steps():
        print(f"==> {step.name}")
        result = subprocess.run(step.command, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f"verification smoke failed at step: {step.name}", file=sys.stderr)
            return result.returncode or 1
    print("verification smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
