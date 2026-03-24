from pathlib import Path
from importlib import import_module

from invest_evolution.application import training

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = PROJECT_ROOT / "src" / "invest_evolution" / "application" / "training"


def test_training_canonical_modules_are_available():
    assert hasattr(training.bootstrap, "initialize_training_services")
    assert hasattr(training.bootstrap, "build_default_training_diagnostics")
    assert hasattr(training.bootstrap, "build_mock_provider")

    assert hasattr(training.controller, "TrainingLifecycleService")
    assert hasattr(training.controller, "TrainingCycleDataService")
    assert hasattr(training.controller, "TrainingExecutionService")
    assert hasattr(training.controller, "TrainingSessionState")

    assert hasattr(training.execution, "ManagerExecutionService")
    assert hasattr(training.execution, "TrainingABService")
    assert hasattr(training.execution, "TrainingSelectionService")
    assert hasattr(training.execution, "TrainingSimulationService")
    assert hasattr(training.execution, "TrainingOutcomeService")
    assert hasattr(training.execution, "build_promotion_record")
    assert hasattr(training.execution, "build_lineage_record")
    assert hasattr(training.execution, "trigger_loss_optimization")

    assert hasattr(training.review, "build_review_input")
    assert hasattr(training.review, "build_review_basis_window")
    assert hasattr(training.review, "TrainingReviewService")
    assert hasattr(training.review, "TrainingReviewStageService")
    assert hasattr(training.review, "ManagerReviewStageService")
    assert hasattr(training.review, "AllocationReviewStageService")

    assert hasattr(training.research, "TrainingResearchService")
    assert hasattr(training.research, "run_validation_orchestrator")
    assert hasattr(training.research, "build_judge_report")

    assert hasattr(training.persistence, "build_cycle_result_persistence_payload")
    assert hasattr(training.observability, "SelfAssessmentSnapshot")
    assert hasattr(training.observability, "build_freeze_report")
    assert hasattr(training.observability, "build_selection_boundary_projection")
    assert hasattr(training.observability, "build_review_eval_projection_boundary")
    assert hasattr(training.observability, "build_outcome_execution_boundary_projection")
    assert hasattr(training.policy, "TrainingPolicyService")
    assert hasattr(training.policy, "TrainingGovernanceService")
    assert hasattr(training.policy, "TrainingExperimentService")
    assert hasattr(training.policy, "governance_from_controller")


def test_training_canonical_modules_are_importable():
    assert callable(import_module("invest_evolution.application.training.bootstrap").initialize_core_runtime)
    assert callable(import_module("invest_evolution.application.training.controller").TrainingCycleDataService.prepare_cycle_context)
    assert callable(import_module("invest_evolution.application.training.execution").TrainingExecutionService.execute_loaded_cycle)
    assert callable(import_module("invest_evolution.application.training.review").build_review_basis_window)
    assert callable(import_module("invest_evolution.application.train").build_train_parser)
    assert callable(import_module("invest_evolution.application.train").run_train_cli)


def test_training_retired_fragment_modules_are_deleted():
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
        assert not (TRAINING_DIR / name).exists(), name
