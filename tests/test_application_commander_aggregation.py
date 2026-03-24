from pathlib import Path
from importlib import import_module

from invest_evolution.application import commander

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMANDER_DIR = PROJECT_ROOT / "src" / "invest_evolution" / "application" / "commander"


def test_commander_canonical_modules_are_available():
    assert callable(commander.bootstrap.initialize_commander_runtime)
    assert callable(commander.bootstrap.build_commander_config_from_args)
    assert hasattr(commander.bootstrap, "PlaybookRegistry")

    assert callable(commander.ops.get_domain_tools)
    assert callable(commander.ops.get_leaderboard_payload)
    assert hasattr(commander.ops, "CommanderControlSurfaceMixin")

    assert callable(commander.runtime.load_persisted_runtime_state)
    assert callable(commander.runtime.get_status_response)
    assert callable(commander.runtime.reload_playbooks_response)
    assert hasattr(commander.runtime, "CommanderRuntimeEventStreamMixin")

    assert callable(commander.status.collect_data_status)
    assert callable(commander.status.build_runtime_diagnostics)
    assert callable(commander.status.build_commander_status_payload)
    assert callable(commander.status.build_training_memory_entry)

    assert callable(commander.workflow.attach_domain_mutating_workflow)
    assert callable(commander.workflow.attach_domain_readonly_workflow)
    assert callable(commander.workflow.ask_runtime)
    assert callable(commander.workflow.start_runtime)
    assert callable(commander.workflow.train_once)


def test_commander_retired_fragment_modules_are_deleted():
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
        assert not (COMMANDER_DIR / name).exists(), name


def test_commander_canonical_modules_are_importable():
    assert callable(import_module("invest_evolution.application.commander.bootstrap").commander_llm_default)
    assert callable(import_module("invest_evolution.application.commander.workflow").attach_domain_mutating_workflow)
    assert callable(import_module("invest_evolution.application.commander.ops").CommanderControlSurfaceMixin.get_managers)
    assert callable(import_module("invest_evolution.application.commander.runtime").get_runtime_diagnostics_response)
    assert callable(import_module("invest_evolution.application.commander.status").collect_data_status)


def test_commander_init_does_not_install_legacy_aliases():
    commander_init = (COMMANDER_DIR / "__init__.py").read_text(encoding="utf-8")

    assert "_LEGACY_MODULE_ALIASES" not in commander_init
    assert "_install_legacy_module_aliases" not in commander_init
    assert "sys.modules[" not in commander_init
    assert "training_summary" not in commander_init


def test_commander_main_delegates_to_canonical_modules():
    commander_main = (
        PROJECT_ROOT / "src" / "invest_evolution" / "application" / "commander_main.py"
    ).read_text(encoding="utf-8")

    assert "CommanderControlSurfaceMixin" in commander_main
    assert "from invest_evolution.application.commander.bootstrap import (" in commander_main
    assert "from invest_evolution.application.commander.ops import (" in commander_main
    assert "from invest_evolution.application.commander.runtime import (" in commander_main
    assert "from invest_evolution.application.commander.status import (" in commander_main
    assert "from invest_evolution.application.commander.workflow import (" in commander_main
    assert "from invest_evolution.application.commander.cli import" not in commander_main


def test_commander_main_exports_facade_runtime_helpers():
    commander_main = import_module("invest_evolution.application.commander_main")
    expected = {
        "CommanderConfig",
        "CommanderRuntime",
        "InvestmentBodyService",
        "STATUS_OK",
        "STATUS_CONFIRMATION_REQUIRED",
        "build_events_tail_response_bundle",
        "build_events_summary_response_bundle",
        "build_runtime_diagnostics_response_bundle",
        "build_training_lab_summary_response_bundle",
        "build_status_response_bundle",
        "get_events_tail_response",
        "get_events_summary_response",
        "get_runtime_diagnostics_response",
        "get_training_lab_summary_response",
        "build_parser",
        "run_async",
        "main",
    }

    assert expected <= set(commander_main.__all__)

    exported_namespace: dict[str, object] = {}
    exec(
        "from invest_evolution.application.commander_main import *",
        {},
        exported_namespace,
    )

    for name in expected:
        assert name in exported_namespace, name
