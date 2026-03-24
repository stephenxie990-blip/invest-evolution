"""Runtime environment guards shared by CLI, scripts, and smoke checks."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_VENV_DIR = PROJECT_ROOT / ".venv"


@dataclass(frozen=True)
class EnvironmentIssue:
    code: str
    message: str
    hint: str = ""


def project_python_candidates(*, project_root: Path | None = None) -> tuple[Path, ...]:
    root = Path(project_root or PROJECT_ROOT).expanduser().resolve()
    venv_root = root / ".venv" / "bin"
    return (
        (venv_root / "python").resolve(),
        (venv_root / "python3").resolve(),
    )


def preferred_project_python(*, project_root: Path | None = None) -> Path | None:
    for candidate in project_python_candidates(project_root=project_root):
        if candidate.exists():
            return candidate
    return None


def interpreter_is_project_managed(
    executable: str | Path | None = None,
    *,
    project_root: Path | None = None,
) -> bool:
    current = Path(executable or sys.executable).expanduser().resolve()
    for candidate in project_python_candidates(project_root=project_root):
        if candidate.exists() and current == candidate:
            return True
    return False


def _missing_modules(modules: Sequence[str]) -> list[str]:
    missing: list[str] = []
    for name in modules:
        normalized = str(name or "").strip()
        if not normalized:
            continue
        if importlib.util.find_spec(normalized) is None:
            missing.append(normalized)
    return missing


def _requests_dependency_issue() -> EnvironmentIssue | None:
    if importlib.util.find_spec("requests") is None:
        return EnvironmentIssue(
            code="missing_requests",
            message="missing required module: requests",
            hint="Run `python3 scripts/bootstrap_env.py` or `uv sync --frozen --extra dev --extra prod`.",
        )

    existing_requests = sys.modules.pop("requests", None)
    try:
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            importlib.import_module("requests")
    finally:
        sys.modules.pop("requests", None)
        if existing_requests is not None:
            sys.modules["requests"] = existing_requests

    for warning_record in recorded:
        category_name = getattr(warning_record.category, "__name__", "")
        if category_name != "RequestsDependencyWarning":
            continue
        return EnvironmentIssue(
            code="unsupported_requests_stack",
            message=str(warning_record.message),
            hint="Use the project-managed `.venv` via `python3 scripts/bootstrap_env.py --reinstall` or `uv run python -m ...`.",
        )
    return None


def collect_environment_issues(
    *,
    required_modules: Sequence[str] = (),
    require_project_python: bool = False,
    validate_requests_stack: bool = False,
) -> list[EnvironmentIssue]:
    issues: list[EnvironmentIssue] = []

    if require_project_python and not interpreter_is_project_managed():
        preferred = preferred_project_python()
        hint = (
            f"Re-run with {preferred} or bootstrap the project env via `python3 scripts/bootstrap_env.py`."
            if preferred is not None
            else "Bootstrap the project env via `python3 scripts/bootstrap_env.py`."
        )
        issues.append(
            EnvironmentIssue(
                code="unsupported_interpreter",
                message=f"current interpreter is not the project-managed runtime: {Path(sys.executable).resolve()}",
                hint=hint,
            )
        )

    missing_modules = _missing_modules(required_modules)
    for name in missing_modules:
        issues.append(
            EnvironmentIssue(
                code="missing_module",
                message=f"missing required module: {name}",
                hint="Run `python3 scripts/bootstrap_env.py` or `uv sync --frozen --extra dev --extra prod`.",
            )
        )

    if validate_requests_stack:
        request_issue = _requests_dependency_issue()
        if request_issue is not None:
            issues.append(request_issue)

    return issues


def render_environment_issues(issues: Sequence[EnvironmentIssue]) -> str:
    rendered = ["runtime environment validation failed:"]
    for issue in issues:
        line = f"- [{issue.code}] {issue.message}"
        if issue.hint:
            line += f" | hint: {issue.hint}"
        rendered.append(line)
    return "\n".join(rendered)


def ensure_environment(
    *,
    required_modules: Sequence[str] = (),
    require_project_python: bool = False,
    validate_requests_stack: bool = False,
    component: str = "runtime",
) -> None:
    issues = collect_environment_issues(
        required_modules=required_modules,
        require_project_python=require_project_python,
        validate_requests_stack=validate_requests_stack,
    )
    if not issues:
        return
    raise RuntimeError(f"{component} environment validation failed\n{render_environment_issues(issues)}")


def environment_check_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the current interpreter and dependency stack.")
    parser.add_argument("--module", dest="modules", action="append", default=[], help="Require an importable module.")
    parser.add_argument(
        "--require-project-python",
        action="store_true",
        help="Fail when the current interpreter is not the project-managed .venv runtime.",
    )
    parser.add_argument(
        "--check-requests-stack",
        action="store_true",
        help="Validate that importing requests does not emit RequestsDependencyWarning.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    issues = collect_environment_issues(
        required_modules=list(args.modules),
        require_project_python=bool(args.require_project_python),
        validate_requests_stack=bool(args.check_requests_stack),
    )
    payload = {
        "ok": not issues,
        "interpreter": str(Path(sys.executable).resolve()),
        "managed_interpreter": interpreter_is_project_managed(),
        "preferred_python": str(preferred_project_python() or ""),
        "issues": [asdict(issue) for issue in issues],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif issues:
        print(render_environment_issues(issues))
    else:
        print(f"runtime environment OK via {payload['interpreter']}")
    return 0 if not issues else 1


def main(argv: Sequence[str] | None = None) -> int:
    return environment_check_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
