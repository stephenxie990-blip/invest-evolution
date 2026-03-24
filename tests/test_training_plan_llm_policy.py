from invest_evolution.application.lab import TrainingLabArtifactStore
from invest_evolution.application.train import SelfLearningController


def test_training_plan_payload_includes_llm_policy(tmp_path):
    store = TrainingLabArtifactStore(
        training_plan_dir=tmp_path / "plans",
        training_run_dir=tmp_path / "runs",
        training_eval_dir=tmp_path / "evals",
    )
    payload = store.build_training_plan_payload(
        rounds=1,
        mock=False,
        source="manual",
        llm={"timeout": 9, "max_retries": 2, "dry_run": True},
    )

    assert payload["llm"]["timeout"] == 9
    assert payload["llm"]["max_retries"] == 2
    assert payload["llm"]["dry_run"] is True


def test_controller_configure_experiment_applies_llm_limits(tmp_path):
    controller = SelfLearningController(output_dir=str(tmp_path / "out"), artifact_log_dir=str(tmp_path / "artifacts"))
    controller.configure_experiment({
        "llm": {"timeout": 3, "max_retries": 1, "dry_run": True},
    })

    assert controller.llm_caller.timeout == 3
    assert controller.llm_caller.max_retries == 1
    assert controller.llm_caller.dry_run is True
    assert controller.llm_optimizer.llm.timeout == 3
    assert controller.llm_optimizer.llm.max_retries == 1
    assert controller.llm_optimizer.llm.dry_run is True
