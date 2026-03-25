from invest_evolution.application.train import (
    OptimizationEvent,
    ReinforcementLearningOptimizer,
    SelfLearningController,
)
from invest_evolution.application.training.controller import (
    TrainingSessionCompatMixin,
    TrainingSessionState,
)


def test_train_facade_still_exports_reinforcement_learning_optimizer():
    optimizer = ReinforcementLearningOptimizer()
    updated = optimizer.get_action(
        "loss_streak_3",
        {"position_size": 0.10, "take_profit_pct": 0.08},
    )

    assert ReinforcementLearningOptimizer.__module__ == (
        "invest_evolution.application.training.controller"
    )
    assert "position_size" in updated
    assert "take_profit_pct" in updated


def test_train_facade_still_exports_optimization_event_contract():
    event = OptimizationEvent(
        cycle_id=3,
        trigger="consecutive_losses",
        stage="review_decision",
        suggestions=["lower exposure"],
    )
    payload = event.to_dict()

    assert OptimizationEvent.__module__ == (
        "invest_evolution.application.training.optimization_event"
    )
    assert payload["cycle_id"] == 3
    assert payload["trigger"] == "consecutive_losses"
    assert payload["stage"] == "review_decision"
    assert payload["suggestions"] == ["lower exposure"]
    assert payload["contract_version"].startswith("optimization_event")


def test_self_learning_controller_uses_session_compat_mixin():
    assert issubclass(SelfLearningController, TrainingSessionCompatMixin)


def test_session_compat_mixin_round_trips_session_state_fields():
    class DummyController(TrainingSessionCompatMixin):
        pass

    controller = DummyController()
    setattr(controller, "session_state", TrainingSessionState())

    controller.current_params = {"position_size": 0.22}
    controller.consecutive_losses = 2
    controller.default_manager_id = "momentum"
    controller.default_manager_config_ref = "configs/momentum_v1.yaml"
    controller.manager_budget_weights = {"momentum": 1.0}
    controller.last_governance_decision = {"mode": "rule"}
    controller.last_feedback_optimization = {"applied": True}
    controller.last_feedback_optimization_cycle_id = 7
    controller.cycle_history = [{"cycle_id": 1}]
    controller.cycle_records = [{"cycle_id": 1, "status": "ok"}]

    assert controller.current_params["position_size"] == 0.22
    assert controller.consecutive_losses == 2
    assert controller.default_manager_id == "momentum"
    assert controller.default_manager_config_ref == "configs/momentum_v1.yaml"
    assert controller.manager_budget_weights == {"momentum": 1.0}
    assert controller.last_governance_decision == {"mode": "rule"}
    assert controller.last_feedback_optimization == {"applied": True}
    assert controller.last_feedback_optimization_cycle_id == 7
    assert controller.cycle_history == [{"cycle_id": 1}]
    assert controller.cycle_records == [{"cycle_id": 1, "status": "ok"}]
