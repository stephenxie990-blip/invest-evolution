from pathlib import Path
import subprocess
import sys

from scripts.bootstrap_env import (
    build_bootstrap_command,
    build_environment_check_command,
    ensure_source_checkout_bridge,
    expected_modules,
)
from scripts.run_verification_smoke import build_smoke_steps


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_env_defaults_to_frozen_dev_and_prod():
    command = build_bootstrap_command()

    assert command == ["uv", "sync", "--frozen", "--extra", "dev", "--extra", "prod"]


def test_bootstrap_env_can_build_reinstall_and_check_commands():
    command = build_bootstrap_command(reinstall=True, check=True, active=True)

    assert "--reinstall" in command
    assert "--check" in command
    assert "--inexact" in command
    assert "--active" in command


def test_bootstrap_env_expected_modules_cover_base_dev_and_prod():
    assert expected_modules(include_dev=True, include_prod=True) == [
        "invest_evolution",
        "pandas",
        "requests",
        "rank_bm25",
        "pytest",
        "pytest_cov",
        "ruff",
        "pyright",
        "gunicorn",
    ]


def test_bootstrap_env_builds_source_checkout_environment_check_command():
    command = build_environment_check_command()

    assert command[0] == str(PROJECT_ROOT / ".venv" / "bin" / "python")
    assert command[1] == str(
        PROJECT_ROOT / "src" / "invest_evolution" / "common" / "environment.py"
    )
    assert "--check-requests-stack" in command
    assert "--require-project-python" in command
    assert command.count("--module") == len(expected_modules())


def test_bootstrap_env_active_environment_check_uses_current_interpreter():
    command = build_environment_check_command(active=True)

    assert command[0] == sys.executable
    assert "--require-project-python" not in command


def test_bootstrap_env_source_checkout_bridge_targets_managed_package_link():
    bridge_path = ensure_source_checkout_bridge()

    assert bridge_path is not None
    assert bridge_path.name == "invest_evolution"
    result = subprocess.run(
        [
            str(PROJECT_ROOT / ".venv" / "bin" / "python"),
            "-c",
            "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('invest_evolution') else 1)",
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert result.returncode == 0


def test_verification_smoke_steps_cover_bootstrap_pytest_ruff_and_pyright():
    steps = build_smoke_steps()

    assert [step.name for step in steps] == [
        "env-bootstrap-check",
        "focused-pytest",
        "focused-ruff",
        "focused-pyright",
    ]
    assert steps[0].command[-1] == "--check"
    assert "tests/test_environment_bootstrap_assets.py" in steps[1].command
    assert "tests/test_deploy_public_surface_smoke.py" in steps[1].command
    assert "tests/test_runtime_read_routes.py" in steps[1].command
    assert "scripts/bootstrap_env.py" in steps[2].command
    assert "tests/test_environment_bootstrap_assets.py" in steps[2].command
    assert "tests/test_deploy_public_surface_smoke.py" in steps[2].command
    assert "src/invest_evolution/interfaces/web/runtime.py" in steps[3].command
    assert "src/invest_evolution/interfaces/web/server.py" in steps[3].command
    assert "tests/test_environment_bootstrap_assets.py" in steps[3].command
    assert "tests/test_deploy_public_surface_smoke.py" in steps[3].command


def test_readme_documents_canonical_bootstrap_and_smoke_paths():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "python3 scripts/bootstrap_env.py" in readme
    assert "python3 scripts/bootstrap_env.py --reinstall" in readme
    assert "uv run python -m invest_evolution.interfaces.cli.commander" in readme
    assert "uv run python -m invest_evolution.interfaces.cli.market_data" in readme
    assert "uv run python -m invest_evolution.interfaces.cli.train" in readme
    assert "uv run python -m invest_evolution.interfaces.web.server" in readme
    assert "uv run python scripts/run_verification_smoke.py" in readme
    assert "uv run invest-freeze-gate --mode quick" in readme
    assert (
        "普通系统解释器下的裸 `python3 -m invest_evolution...` 不属于源码 checkout 的稳定契约"
        in readme
    )


def test_config_governance_doc_does_not_reference_removed_runtime_path_script():
    governance_doc = (PROJECT_ROOT / "docs" / "CONFIG_GOVERNANCE.md").read_text(
        encoding="utf-8"
    )

    assert "scripts/migrate_runtime_artifact_paths.py" not in governance_doc
    assert "/api/runtime_paths" in governance_doc
