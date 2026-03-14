import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "external",
    "logs",
    "outputs",
    "runtime",
    ".learnings",
    ".trae",
}


def _py_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def _read_python_source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_project_code_does_not_import_src_package_internally():
    allowed_files = {
        PROJECT_ROOT / "tests" / "test_structure_guards.py",
    }

    for path in _py_files(PROJECT_ROOT):
        if path in allowed_files:
            continue
        if path.is_relative_to(SRC_DIR):
            continue

        tree = ast.parse(_read_python_source(path), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "src" and not alias.name.startswith("src."), (
                        f"{path} should import root modules instead of `{alias.name}`"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "src" and not module.startswith("src."), (
                    f"{path} should import root modules instead of `{module}`"
                )


def test_root_modules_import_cleanly():
    import commander
    from invest import foundation

    assert hasattr(commander, "CommanderRuntime")
    assert hasattr(commander, "StrategyGeneRegistry")
    assert hasattr(foundation, "SimulatedTrader")
    assert hasattr(foundation, "Position")


def test_legacy_invest_packages_are_removed():
    assert not (PROJECT_ROOT / "invest" / "selection").exists()
    assert not (PROJECT_ROOT / "invest" / "trading").exists()
    assert not (PROJECT_ROOT / "invest" / "evaluation").exists()
    assert not (PROJECT_ROOT / "invest" / "optimization.py").exists()
    assert not (PROJECT_ROOT / "invest" / "core.py").exists()


def test_root_python_surface_is_intentional():
    allowed = {
        'commander.py',
        'gunicorn.conf.py',
        'train.py',
        'web_server.py',
        'wsgi.py',
    }
    root_python = {path.name for path in PROJECT_ROOT.glob('*.py')}
    assert root_python == allowed
