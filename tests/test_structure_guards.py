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
    "archive",
    ".learnings",
    ".trae",
    "参考项目",
}


def _py_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def test_src_modules_are_compatibility_wrappers_only():
    for path in _py_files(SRC_DIR):
        if path.name == "__init__.py":
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]

        assert len(body) == 1, f"{path} must only contain one compatibility import"

        node = body[0]
        assert isinstance(node, ast.ImportFrom), f"{path} must use from-import wrapper"
        assert node.level == 0, f"{path} must import from project root module"
        assert node.module == path.stem, f"{path} must re-export `{path.stem}`"
        assert len(node.names) == 1 and node.names[0].name == "*", f"{path} must re-export all public names"


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


def test_src_compatibility_imports_expose_root_symbols():
    import commander
    import src.commander as src_commander
    import trading
    import src.trading as src_trading

    assert src_commander.CommanderRuntime is commander.CommanderRuntime
    assert src_commander.StrategyGeneRegistry is commander.StrategyGeneRegistry
    assert src_trading.SimulatedTrader is trading.SimulatedTrader
    assert src_trading.Position is trading.Position
