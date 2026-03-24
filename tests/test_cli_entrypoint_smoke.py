from __future__ import annotations

import builtins
import os
import subprocess
import sys
from pathlib import Path

import pytest

from invest_evolution.interfaces.cli import commander as commander_cli
from invest_evolution.interfaces.cli import market_data as market_data_cli
from invest_evolution.interfaces.cli import train as train_cli
from scripts.bootstrap_env import ensure_source_checkout_bridge

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _managed_python() -> str:
    ensure_source_checkout_bridge()
    for candidate in (
        PROJECT_ROOT / ".venv" / "bin" / "python",
        PROJECT_ROOT / ".venv" / "bin" / "python3",
    ):
        if candidate.exists():
            return str(candidate)
    raise AssertionError("expected project-managed python in .venv")


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def _managed_script(name: str) -> str:
    candidate = PROJECT_ROOT / ".venv" / "bin" / name
    if not candidate.exists():
        raise AssertionError(f"expected managed console script: {candidate}")
    return str(candidate)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_managed_python_can_invoke_supported_cli_entrypoints():
    python_bin = _managed_python()
    commands = [
        [python_bin, "-m", "invest_evolution.interfaces.cli.commander", "--help"],
        [python_bin, "-m", "invest_evolution.interfaces.cli.train", "--help"],
        [python_bin, "-m", "invest_evolution.interfaces.cli.market_data", "--help"],
        [python_bin, "-m", "invest_evolution.interfaces.cli.runtime", "--help"],
        [python_bin, "-m", "invest_evolution.interfaces.web.server", "--help"],
    ]

    for command in commands:
        result = _run(*command)
        assert result.returncode == 0, (
            f"command failed: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def test_managed_console_scripts_expose_help_for_canonical_entrypoints():
    scripts = [
        "invest-commander",
        "invest-train",
        "invest-runtime",
        "invest-data",
        "invest-freeze-gate",
        "invest-release-verify",
    ]

    for script_name in scripts:
        result = _run(_managed_script(script_name), "--help")
        assert result.returncode == 0, (
            f"console script failed: {script_name}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    train_help = _run(_managed_script("invest-train"), "--help")
    assert "Commander" in train_help.stdout or "Commander" in train_help.stderr


def test_managed_python_can_import_canonical_wsgi_entrypoint():
    python_bin = _managed_python()
    result = _run(
        python_bin,
        "-c",
        "from invest_evolution.interfaces.web.wsgi import app; "
        "import sys; sys.stdout.write(type(app).__name__)",
    )

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert result.stdout.strip()


def test_managed_python_gunicorn_check_config_accepts_canonical_wsgi_entrypoint():
    python_bin = _managed_python()
    result = _run(
        python_bin,
        "-m",
        "gunicorn",
        "--check-config",
        "-c",
        "gunicorn.conf.py",
        "invest_evolution.interfaces.web.wsgi:app",
    )

    assert result.returncode == 0, (
        "gunicorn should accept the canonical WSGI entrypoint during config validation\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.parametrize(
    ("module", "argv", "target_import"),
    [
        (commander_cli, ["invest-commander", "--help"], "invest_evolution.application.commander_main"),
        (train_cli, ["invest-train", "--help"], "invest_evolution.application.train"),
        (market_data_cli, ["invest-data", "--help"], "invest_evolution.market_data.__main__"),
    ],
)
def test_help_fallback_returns_non_zero_when_backend_import_fails(
    monkeypatch,
    capsys,
    module,
    argv,
    target_import,
):
    real_import = builtins.__import__

    def _failing_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == target_import:
            raise ModuleNotFoundError(f"No module named '{target_import}'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(sys, "argv", list(argv))
    monkeypatch.setattr(builtins, "__import__", _failing_import)

    assert module.main() == 1
    captured = capsys.readouterr()
    assert "backend import failed" in captured.err
