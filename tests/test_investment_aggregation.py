from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "invest_evolution"


def test_investment_package_surface_retires_thin_domains():
    source = (SRC_ROOT / "investment" / "__init__.py").read_text(encoding="utf-8")

    for retired in ("allocator", "artifacts", "capabilities", "leaderboard", "portfolio", "services"):
        assert f'"{retired}"' not in source


def test_investment_canonical_subpackages_exist():
    investment_root = SRC_ROOT / "investment"
    for path in (
        investment_root / "agents",
        investment_root / "contracts",
        investment_root / "evolution",
        investment_root / "factors",
        investment_root / "foundation",
        investment_root / "governance",
        investment_root / "managers",
        investment_root / "research",
        investment_root / "runtimes",
        investment_root / "shared",
    ):
        assert path.exists(), path


def test_governance_research_and_runtime_owners_absorb_retired_investment_modules():
    assert (SRC_ROOT / "investment" / "governance" / "engine.py").exists()
    assert (SRC_ROOT / "investment" / "governance" / "planning.py").exists()
    assert (SRC_ROOT / "investment" / "research" / "artifacts.py").exists()
    assert (SRC_ROOT / "investment" / "shared" / "policy.py").exists()
    assert (SRC_ROOT / "investment" / "runtimes" / "ops.py").exists()


def test_evolution_and_research_are_collapsed_to_stable_analysis_modules():
    evolution_root = SRC_ROOT / "investment" / "evolution"
    research_root = SRC_ROOT / "investment" / "research"

    assert {path.name for path in evolution_root.glob("*.py")} == {
        "__init__.py",
        "analysis.py",
        "engine.py",
        "mutation.py",
        "optimization.py",
        "orchestrator.py",
    }
    assert {path.name for path in research_root.glob("*.py")} == {
        "__init__.py",
        "analysis.py",
        "artifacts.py",
        "case_store.py",
    }
