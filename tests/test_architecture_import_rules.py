import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "invest_evolution"
CHECK_ROOTS = (
    PROJECT_ROOT / "src",
    PROJECT_ROOT / "tests",
    PROJECT_ROOT / "scripts",
)
RETIRED_IMPORT_PREFIXES = ("app", "brain", "invest", "market_data", "config")


def _py_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_retired_source_packages_are_absent():
    for rel_path in ("app", "brain", "invest", "market_data"):
        assert not (PROJECT_ROOT / rel_path).exists(), f"retired package still present: {rel_path}"


def test_all_python_source_lives_under_src_main_package():
    assert SRC_ROOT.exists()
    assert (SRC_ROOT / "application").exists()
    assert (SRC_ROOT / "interfaces").exists()
    assert (SRC_ROOT / "investment").exists()
    assert (SRC_ROOT / "agent_runtime").exists()
    assert (SRC_ROOT / "market_data").exists()


def test_repo_python_files_do_not_import_retired_top_level_packages():
    for root in CHECK_ROOTS:
        for path in _py_files(root):
            for module in _imports(path):
                assert not any(
                    module == prefix or module.startswith(f"{prefix}.")
                    for prefix in RETIRED_IMPORT_PREFIXES
                ), f"{path} still imports retired module {module}"


def test_investment_layer_does_not_depend_on_application_or_interfaces():
    forbidden = ("invest_evolution.application", "invest_evolution.interfaces")
    for path in _py_files(SRC_ROOT / "investment"):
        for module in _imports(path):
            assert not any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in forbidden
            ), f"{path} should not depend on higher layer {module}"


def test_application_layer_does_not_depend_on_interfaces():
    forbidden = "invest_evolution.interfaces"
    for path in _py_files(SRC_ROOT / "application"):
        for module in _imports(path):
            assert module != forbidden and not module.startswith(f"{forbidden}."), (
                f"{path} should not depend on interface layer {module}"
            )


def test_readme_describes_canonical_entrypoint_layout():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "src/invest_evolution/application/commander_main.py" in readme
    assert "src/invest_evolution/application/commander/bootstrap.py" in readme
    assert "src/invest_evolution/application/commander/ops.py" in readme
    assert "src/invest_evolution/application/commander/runtime.py" in readme
    assert "src/invest_evolution/application/commander/status.py" in readme
    assert "src/invest_evolution/application/commander/workflow.py" in readme
    assert "src/invest_evolution/application/train.py" in readme
    assert "src/invest_evolution/application/training/bootstrap.py" in readme
    assert "src/invest_evolution/application/training/controller.py" in readme
    assert "src/invest_evolution/application/training/execution.py" in readme
    assert "src/invest_evolution/application/training/review.py" in readme
    assert "src/invest_evolution/application/training/policy.py" in readme
    assert "src/invest_evolution/interfaces/web/server.py" in readme
    assert "src/invest_evolution/market_data/__main__.py" in readme
    assert "training_summary.py" not in readme
    assert "兼容壳" not in readme
    assert "以下根目录文件仍可继续使用" not in readme
    assert "`/legacy`" not in readme
    assert "`/app`" not in readme
    assert "historical compatibility" not in readme.lower()
