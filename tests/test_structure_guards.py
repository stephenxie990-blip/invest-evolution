import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "logs",
    "outputs",
    "runtime",
    "历史归档区",
    ".learnings",
    ".trae",
}


def _py_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def test_project_code_does_not_import_src_package_internally():
    allowed_files = {
        PROJECT_ROOT / "tests" / "test_structure_guards.py",
    }

    for path in _py_files(PROJECT_ROOT):
        if path in allowed_files:
            continue
        if path.is_relative_to(SRC_DIR):
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
