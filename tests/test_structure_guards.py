import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "invest_evolution"
RETIRED_ROOT_ENTRYPOINTS = (
    "commander.py",
    "train.py",
    "web_server.py",
    "wsgi.py",
    "llm_gateway.py",
    "llm_router.py",
)


def test_root_python_surface_is_clean():
    root_python = {path.name for path in PROJECT_ROOT.glob("*.py")}
    assert root_python == {"gunicorn.conf.py"}


def test_retired_root_entrypoints_are_removed():
    for rel_path in RETIRED_ROOT_ENTRYPOINTS:
        assert not (PROJECT_ROOT / rel_path).exists(), f"retired entrypoint still present: {rel_path}"


def test_gitignore_covers_workspace_outputs_and_agent_metadata():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    for entry in (
        ".workspace/**",
        "outputs/",
        "runtime/**",
        ".Codex/",
        "agent_settings/",
        ".learnings/",
        ".phase1-verify/",
        "findings.md",
        "progress.md",
        "task_plan.md",
        "__pycache__/",
        ".ruff_cache/",
        ".coverage",
        ".DS_Store",
    ):
        assert entry in gitignore


def test_wsgi_surface_is_side_effect_light():
    wsgi_source = (SRC_ROOT / "interfaces" / "web" / "wsgi.py").read_text(encoding="utf-8")

    assert "from invest_evolution.interfaces.web.server import app" in wsgi_source
    assert "bootstrap_embedded_runtime_if_enabled" not in wsgi_source
    assert "CommanderRuntime(" not in wsgi_source
    assert "CommanderConfig(" not in wsgi_source


def test_active_web_surface_has_no_removed_ui_tombstones():
    server_source = (SRC_ROOT / "interfaces" / "web" / "server.py").read_text(encoding="utf-8")

    assert '@app.route("/legacy")' not in server_source
    assert '@app.route("/app")' not in server_source
    assert '@app.route("/app/<path:asset_path>")' not in server_source
    assert "已移除 UI" not in server_source


def test_repo_no_longer_declares_historical_compatibility_marker():
    pytest_ini = (PROJECT_ROOT / "pytest.ini").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "historical_compatibility" not in pytest_ini
    assert "historical_compatibility" not in pyproject


def test_fixture_and_archive_roots_exist():
    assert (PROJECT_ROOT / "tests" / "fixtures").exists()
    assert (PROJECT_ROOT / "docs" / "archive").exists()


def test_training_canonical_shape_matches_final_collapse():
    training_dir = SRC_ROOT / "application" / "training"
    assert {path.name for path in training_dir.glob("*.py")} == {
        "__init__.py",
        "bootstrap.py",
        "controller.py",
        "execution.py",
        "observability.py",
        "persistence.py",
        "policy.py",
        "research.py",
        "review.py",
    }


def test_training_retired_fragment_modules_are_removed():
    training_dir = SRC_ROOT / "application" / "training"
    for name in (
        "boundary.py",
        "cycle_data.py",
        "diagnostics.py",
        "execution_pipeline.py",
        "launcher.py",
        "manager_execution.py",
        "manager_runtime.py",
        "promotion.py",
        "review_analysis.py",
        "review_contracts.py",
        "selection.py",
        "session_state.py",
        "simulation.py",
        "experiment_protocol.py",
        "lifecycle_services.py",
    ):
        assert not (training_dir / name).exists(), name


def test_train_facade_points_at_canonical_training_owners():
    train_source = (SRC_ROOT / "application" / "train.py").read_text(encoding="utf-8")

    assert "from invest_evolution.application.training.bootstrap import (" in train_source
    assert "from invest_evolution.application.training.execution import (" in train_source
    assert "from invest_evolution.application.training.controller import (" in train_source
    assert "from invest_evolution.application.training.launcher import" not in train_source
    assert "from invest_evolution.application.training.diagnostics import" not in train_source
    assert "def build_train_parser(" in train_source
    assert "def run_train_cli(" in train_source


def test_controller_owns_session_and_cycle_context_after_collapse():
    controller_source = (SRC_ROOT / "application" / "training" / "controller.py").read_text(
        encoding="utf-8"
    )

    assert "class TrainingSessionState" in controller_source
    assert "class TrainingCycleDataService" in controller_source
    assert "from invest_evolution.application.training.session_state import" not in controller_source
    assert "from invest_evolution.application.training.cycle_data import" not in controller_source
    assert "from invest_evolution.application.training.execution import (" in controller_source


def test_commander_canonical_shape_matches_final_collapse():
    commander_dir = SRC_ROOT / "application" / "commander"
    assert {path.name for path in commander_dir.glob("*.py")} == {
        "__init__.py",
        "bootstrap.py",
        "ops.py",
        "presentation.py",
        "runtime.py",
        "status.py",
        "workflow.py",
    }


def test_commander_retired_fragment_modules_are_removed():
    commander_dir = SRC_ROOT / "application" / "commander"
    for name in (
        "actions.py",
        "ask.py",
        "cli.py",
        "config.py",
        "control_surface.py",
        "identity.py",
        "playbook_registry.py",
        "runtime_control.py",
        "runtime_events.py",
        "runtime_state.py",
        "status_diagnostics.py",
        "status_training_lab.py",
        "training_summary.py",
    ):
        assert not (commander_dir / name).exists(), name


def test_commander_main_points_at_canonical_command_owners():
    commander_source = (SRC_ROOT / "application" / "commander_main.py").read_text(encoding="utf-8")

    assert "from invest_evolution.application.commander.bootstrap import (" in commander_source
    assert "from invest_evolution.application.commander.ops import (" in commander_source
    assert "from invest_evolution.application.commander.runtime import (" in commander_source
    assert "from invest_evolution.application.commander.status import (" in commander_source
    assert "from invest_evolution.application.commander.workflow import (" in commander_source
    assert "from invest_evolution.application.commander.cli import" not in commander_source
    assert "from invest_evolution.application.commander.config import" not in commander_source
    assert "from invest_evolution.application.commander.control_surface import" not in commander_source
    assert "_COMMANDER_CONTROL_SURFACE_EXPORTS" not in commander_source


def test_commander_ops_does_not_route_internal_dependencies_back_through_commander_main():
    ops_source = (SRC_ROOT / "application" / "commander" / "ops.py").read_text(
        encoding="utf-8"
    )

    assert "import invest_evolution.application.commander_main" not in ops_source
    assert "from invest_evolution.application.commander_main import" not in ops_source
    assert "def _commander_module(" not in ops_source
    assert "from invest_evolution.application.config_surface import (" in ops_source
    assert "from invest_evolution.application.commander.workflow import (" in ops_source
    assert "from invest_evolution.application.commander.status import (" in ops_source
    assert "from invest_evolution.application.research_services import (" in ops_source


def test_web_server_depends_on_runtime_layer_not_commander_main_types():
    server_source = (SRC_ROOT / "interfaces" / "web" / "server.py").read_text(
        encoding="utf-8"
    )

    assert "from invest_evolution.application.commander_main import" not in server_source
    assert "class _CommanderConfigProxy" not in server_source
    assert "class _CommanderRuntimeProxy" not in server_source
    assert "load_default_commander_runtime_types" in server_source


def test_web_runtime_owns_default_commander_type_loading():
    runtime_source = (SRC_ROOT / "interfaces" / "web" / "runtime.py").read_text(
        encoding="utf-8"
    )

    assert "def load_default_commander_runtime_types(" in runtime_source
    assert "from invest_evolution.application.commander_main import (" in runtime_source
    assert "from invest_evolution.application.config_surface import get_runtime_paths_payload" in runtime_source


def test_web_canonical_shape_matches_final_collapse():
    web_dir = SRC_ROOT / "interfaces" / "web"
    assert {path.name for path in web_dir.glob("*.py")} == {
        "__init__.py",
        "contracts.py",
        "presentation.py",
        "routes.py",
        "runtime.py",
        "server.py",
        "wsgi.py",
    }


def test_web_retired_fragment_modules_are_removed():
    assert not (SRC_ROOT / "interfaces" / "web" / "http.py").exists()
    assert not (SRC_ROOT / "interfaces" / "web" / "registry.py").exists()
    assert not (SRC_ROOT / "interfaces" / "web" / "runtime_facade.py").exists()
    assert not (SRC_ROOT / "interfaces" / "web" / "state.py").exists()
    assert not (SRC_ROOT / "interfaces" / "web" / "routes").exists()


def test_runtime_output_samples_are_not_kept_in_repo_root():
    assert not (PROJECT_ROOT / "outputs" / "leaderboard.json").exists()
    assert (PROJECT_ROOT / "tests" / "fixtures" / "benchmarks" / "leaderboard.sample.json").exists()
    assert (
        PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "benchmarks"
        / "release_gate_divergence_report.sample.json"
    ).exists()


def test_retired_investment_directories_are_removed():
    investment_root = SRC_ROOT / "investment"
    for name in (
        "allocator",
        "artifacts",
        "capabilities",
        "leaderboard",
        "portfolio",
        "services",
    ):
        assert not (investment_root / name).exists(), name


def test_investment_contracts_are_collapsed_to_canonical_cluster_files():
    contracts_root = SRC_ROOT / "investment" / "contracts"
    assert {path.name for path in contracts_root.glob("*.py")} == {
        "__init__.py",
        "core.py",
        "reports.py",
    }


def test_no_direct_sqlite_connect_outside_market_data_repository():
    for relative_dir in (
        SRC_ROOT / "config",
        SRC_ROOT / "application",
        SRC_ROOT / "interfaces",
        SRC_ROOT / "investment",
    ):
        for path in relative_dir.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "sqlite3.connect(" not in source, f"direct sqlite connect leaked outside data layer: {path}"


def test_industry_registry_reads_industry_snapshot_via_repository_boundary():
    config_source = (SRC_ROOT / "config" / "__init__.py").read_text(encoding="utf-8")

    assert "repository.read_industry_map_snapshot()" in config_source
    assert "SELECT code, industry FROM security_master" not in config_source


def test_public_api_surface_matches_routes_contract_and_active_doc():
    import invest_evolution.interfaces.web.server as web_server

    contract = json.loads(
        (PROJECT_ROOT / "docs" / "contracts" / "runtime-api-contract.v2.json").read_text(
            encoding="utf-8"
        )
    )
    contract_surface = {
        (str(item["method"]).upper(), str(item["path"]))
        for item in contract["endpoints"]
    }

    actual_surface: set[tuple[str, str]] = set()
    for rule in web_server.app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        normalized_path = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", rule.rule)
        for method in sorted((rule.methods or set()) & {"GET", "POST"}):
            actual_surface.add((method, normalized_path))

    compatibility_doc = (PROJECT_ROOT / "docs" / "COMPATIBILITY_SURFACE.md").read_text(
        encoding="utf-8"
    )
    documented_surface = {
        (match.group(1), match.group(2))
        for match in re.finditer(r"`(GET|POST) (/api/[^`]+)`", compatibility_doc)
    }

    assert actual_surface == contract_surface
    assert documented_surface == contract_surface


def test_retired_external_api_shortcuts_are_absent_from_routes_and_contract():
    routes_source = (SRC_ROOT / "interfaces" / "web" / "routes.py").read_text(encoding="utf-8")
    contract = json.loads((PROJECT_ROOT / "docs" / "contracts" / "runtime-api-contract.v2.json").read_text(encoding="utf-8"))
    endpoint_paths = {item["path"] for item in contract["endpoints"]}

    for path in (
        "/api/train",
        "/api/leaderboard",
        "/api/allocator",
        "/api/governance/preview",
        "/api/managers",
        "/api/playbooks",
        "/api/playbooks/reload",
        "/api/cron",
        "/api/cron/{job_id}",
        "/api/memory",
        "/api/memory/{record_id}",
        "/api/data/capital_flow",
        "/api/data/dragon_tiger",
        "/api/data/intraday_60m",
        "/api/lab/status/quick",
        "/api/lab/status/deep",
        "/api/contracts",
    ):
        assert f'@app.route("{path}"' not in routes_source
        assert path not in endpoint_paths


def test_active_docs_define_public_vs_internal_contract_boundary():
    compatibility_doc = (PROJECT_ROOT / "docs" / "COMPATIBILITY_SURFACE.md").read_text(
        encoding="utf-8"
    )
    agent_doc = (PROJECT_ROOT / "docs" / "AGENT_INTERACTION.md").read_text(
        encoding="utf-8"
    )

    assert "## Contract 分层" in compatibility_doc
    assert "Internal runtime/agent contract" in compatibility_doc
    assert "不等价于 public Web/API surface" in compatibility_doc
    assert "bounded_workflow.v2" in compatibility_doc
    assert "task_bus.v2" in compatibility_doc
    assert "task_coverage.v2" in compatibility_doc
    assert "artifact_taxonomy.v2" in compatibility_doc
    assert "/api/agent_prompts" in compatibility_doc
    assert "不是 `agent_runtime` capability registry" in compatibility_doc
    assert "## 9. Internal Contract Boundary" in agent_doc
    assert "runtime-api-contract.v2" in agent_doc
    assert "bounded_workflow.v2" in agent_doc
    assert "task_bus.v2" in agent_doc
    assert "不是外部 deploy contract" in agent_doc
    assert "不等价于新的 public Web API 面" in agent_doc


def test_state_backed_web_runtime_reads_persisted_state_without_commander_bootstrap_proxy():
    runtime_source = (SRC_ROOT / "interfaces" / "web" / "runtime.py").read_text(
        encoding="utf-8"
    )

    assert "from invest_evolution.application.commander.runtime import load_persisted_runtime_state" not in runtime_source
    assert "def _load_persisted_runtime_state_payload(" in runtime_source


def test_web_routes_register_config_surface_via_application_owned_specs():
    routes_source = (SRC_ROOT / "interfaces" / "web" / "routes.py").read_text(
        encoding="utf-8"
    )
    config_surface_source = (SRC_ROOT / "application" / "config_surface.py").read_text(
        encoding="utf-8"
    )

    assert "build_config_surface_route_specs" in routes_source
    assert "def _validate_runtime_paths_update(" not in routes_source
    assert "def _validate_evolution_config_update(" not in routes_source
    assert "class ConfigSurfaceRouteSpec" in config_surface_source
    assert "def validate_control_plane_patch(" in config_surface_source
    assert "def build_public_evolution_config_payload(" in config_surface_source
