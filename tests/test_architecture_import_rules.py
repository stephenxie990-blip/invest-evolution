import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


RULES = {
    "invest/contracts": {"invest.agents", "invest.meetings", "invest.evolution", "app.train", "app.commander"},
    "invest/foundation": {"invest.agents", "invest.meetings", "invest.models"},
    "invest/models": {"invest.agents", "invest.meetings"},
    "app/application": {"flask", "app.interfaces.web", "app.web_server"},
}


def _py_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_new_v2_layers_respect_import_rules():
    for rel_root, forbidden_prefixes in RULES.items():
        root = PROJECT_ROOT / rel_root
        for path in _py_files(root):
            imports = _imports(path)
            for module in imports:
                assert not any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden_prefixes), (
                    f"{path} should not import forbidden module {module}"
                )



def test_legacy_packages_removed_from_tree():
    for rel in (
        "invest/selection",
        "invest/trading",
        "invest/evaluation",
        "invest/optimization.py",
        "invest/core.py",
    ):
        assert not (PROJECT_ROOT / rel).exists(), f"legacy path should be removed: {rel}"


def test_phase6_interface_and_application_packages_exist():
    for rel in (
        "app/application",
        "app/interfaces",
        "app/interfaces/web",
        "invest/services",
        "market_data/services",
    ):
        assert (PROJECT_ROOT / rel).exists(), f"expected Phase 6 package missing: {rel}"
