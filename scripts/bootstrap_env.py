from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
ENVIRONMENT_CHECK_SCRIPT = SRC_ROOT / "invest_evolution" / "common" / "environment.py"
_SOURCE_PACKAGE_LINK = "invest_evolution"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def expected_modules(
    *, include_dev: bool = True, include_prod: bool = True
) -> list[str]:
    modules = ["invest_evolution", "pandas", "requests", "rank_bm25"]
    if include_dev:
        modules.extend(["pytest", "pytest_cov", "ruff", "pyright"])
    if include_prod:
        modules.append("gunicorn")
    return modules


def build_bootstrap_command(
    *,
    frozen: bool = True,
    include_dev: bool = True,
    include_prod: bool = True,
    reinstall: bool = False,
    check: bool = False,
    active: bool = False,
) -> list[str]:
    command = ["uv", "sync"]
    if frozen:
        command.append("--frozen")
    if include_dev:
        command.extend(["--extra", "dev"])
    if include_prod:
        command.extend(["--extra", "prod"])
    if reinstall:
        command.append("--reinstall")
    if check:
        command.append("--check")
        command.append("--inexact")
    if active:
        command.append("--active")
    return command


def _target_python(*, active: bool = False) -> Path:
    if active:
        return Path(sys.executable)
    return PROJECT_ROOT / ".venv" / "bin" / "python"


def _target_site_packages(*, active: bool = False) -> Path | None:
    python_bin = _target_python(active=active)
    if not python_bin.exists():
        return None
    result = subprocess.run(
        [
            str(python_bin),
            "-c",
            (
                "import json, site; "
                "print(json.dumps(site.getsitepackages(), ensure_ascii=False))"
            ),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return None
    for entry in payload:
        candidate = Path(str(entry)).expanduser()
        if candidate.name == "site-packages":
            return candidate
    return None


def _clear_hidden_flag(path: Path) -> bool:
    if shutil.which("chflags") is None:
        return False
    if not path.exists():
        return False
    result = subprocess.run(
        ["chflags", "nohidden", str(path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _unhide_site_packages_pth_files(site_packages: Path) -> int:
    refreshed = 0
    for path in site_packages.glob("*.pth"):
        if _clear_hidden_flag(path):
            refreshed += 1
    return refreshed


def _target_python_can_import_project(*, active: bool = False) -> bool:
    python_bin = _target_python(active=active)
    if not python_bin.exists():
        return False
    result = subprocess.run(
        [
            str(python_bin),
            "-c",
            (
                "import importlib.util, sys; "
                "raise SystemExit(0 if importlib.util.find_spec('invest_evolution') else 1)"
            ),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def ensure_source_checkout_bridge(*, active: bool = False) -> Path | None:
    if active:
        return None
    site_packages = _target_site_packages(active=active)
    if site_packages is None:
        return None
    site_packages.mkdir(parents=True, exist_ok=True)
    refreshed_pth = _unhide_site_packages_pth_files(site_packages)
    if refreshed_pth:
        print(
            f"[bootstrap-env] normalized visibility on {refreshed_pth} site-packages .pth file(s)"
        )
    stale_sitecustomize = site_packages / "sitecustomize.py"
    if stale_sitecustomize.exists():
        payload = stale_sitecustomize.read_text(encoding="utf-8")
        if "invest-evolution source checkout bridge" in payload:
            stale_sitecustomize.unlink()
    stale_usercustomize = site_packages / "usercustomize.py"
    if stale_usercustomize.exists():
        payload = stale_usercustomize.read_text(encoding="utf-8")
        if "_SRC_ROOT = Path(" in payload and "sys.path.insert(0" in payload:
            stale_usercustomize.unlink()
    bridge_path = site_packages / _SOURCE_PACKAGE_LINK
    if _target_python_can_import_project(active=active) and bridge_path.exists():
        return bridge_path
    source_package = SRC_ROOT / _SOURCE_PACKAGE_LINK
    if bridge_path.is_symlink():
        if bridge_path.resolve() == source_package.resolve():
            return bridge_path
        bridge_path.unlink()
    elif bridge_path.exists():
        if bridge_path.is_dir():
            shutil.rmtree(bridge_path)
        else:
            bridge_path.unlink()
    try:
        bridge_path.symlink_to(source_package, target_is_directory=True)
    except OSError:
        shutil.copytree(source_package, bridge_path)
    print(f"[bootstrap-env] refreshed source checkout bridge: {bridge_path}")
    return bridge_path


def build_environment_check_command(
    *,
    include_dev: bool = True,
    include_prod: bool = True,
    active: bool = False,
) -> list[str]:
    modules = expected_modules(include_dev=include_dev, include_prod=include_prod)
    command = [
        str(_target_python(active=active)),
        str(ENVIRONMENT_CHECK_SCRIPT),
        "--check-requests-stack",
    ]
    if not active:
        command.append("--require-project-python")
    for module in modules:
        command.extend(["--module", module])
    return command


def verify_environment(
    *, include_dev: bool = True, include_prod: bool = True, active: bool = False
) -> int:
    from invest_evolution.common.environment import preferred_project_python

    modules = expected_modules(include_dev=include_dev, include_prod=include_prod)
    python_bin = _target_python(active=active)
    command = build_environment_check_command(
        include_dev=include_dev,
        include_prod=include_prod,
        active=active,
    )
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        rendered = ", ".join(modules)
        preferred = preferred_project_python()
        if preferred is not None:
            print(f"[bootstrap-env] expected verified environment via {preferred}")
        print(
            f"[bootstrap-env] runtime validation failed for expected modules: {rendered}"
        )
        return result.returncode or 1
    print(f"[bootstrap-env] verified imports via {python_bin}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap the canonical project environment with uv."
    )
    parser.add_argument(
        "--no-frozen",
        action="store_true",
        help="Allow uv to update the lockfile during sync.",
    )
    parser.add_argument(
        "--no-dev", action="store_true", help="Exclude development dependencies."
    )
    parser.add_argument(
        "--no-prod", action="store_true", help="Exclude production dependencies."
    )
    parser.add_argument(
        "--reinstall",
        action="store_true",
        help="Reinstall all packages and refresh console-script shebangs.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify that the current environment matches the lockfile.",
    )
    parser.add_argument(
        "--active",
        action="store_true",
        help="Sync the active virtual environment instead of the project-managed .venv.",
    )
    parser.add_argument(
        "--skip-import-check",
        action="store_true",
        help="Skip post-sync import verification.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if shutil.which("uv") is None:
        print("[bootstrap-env] missing required executable: uv")
        return 1

    command = build_bootstrap_command(
        frozen=not args.no_frozen,
        include_dev=not args.no_dev,
        include_prod=not args.no_prod,
        reinstall=args.reinstall,
        check=args.check,
        active=args.active,
    )
    print(f"[bootstrap-env] cwd={PROJECT_ROOT}")
    print(f"[bootstrap-env] command={' '.join(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        return result.returncode or 1
    ensure_source_checkout_bridge(active=args.active)
    if args.skip_import_check:
        return 0
    return verify_environment(
        include_dev=not args.no_dev,
        include_prod=not args.no_prod,
        active=args.active,
    )


if __name__ == "__main__":
    raise SystemExit(main())
