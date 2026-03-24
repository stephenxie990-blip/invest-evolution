from types import SimpleNamespace

import pytest

from invest_evolution.application.training.controller import (
    TrainingSessionState,
    update_session_current_params,
)
from invest_evolution.application.training.execution import (
    apply_regime_runtime_profile,
    apply_safety_override,
    begin_cycle_runtime_window,
    build_regime_runtime_profile,
    finalize_cycle_runtime_window,
    resolve_active_runtime_params,
    resolve_effective_runtime_params,
    resolve_entry_threshold_spec,
)


class _RuntimeStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, float | int | bool]] = []

    def update_runtime_overrides(self, payload):
        self.calls.append(dict(payload))


def _build_controller(params: dict[str, float | int | bool]):
    runtime = _RuntimeStub()
    controller = SimpleNamespace(
        session_state=TrainingSessionState(current_params=dict(params)),
        manager_runtime=runtime,
        risk_policy={},
        review_policy={},
        regime_controls={},
        strategy_family="momentum",
    )
    return controller, runtime


def test_begin_finalize_runtime_window_rolls_back_illegal_mutation():
    controller, runtime = _build_controller({"position_size": 0.2, "cash_reserve": 0.1})

    started = begin_cycle_runtime_window(controller, cycle_id=12)
    assert started == {"position_size": 0.2, "cash_reserve": 0.1}

    update_session_current_params(controller, {"position_size": 0.31})

    summary = finalize_cycle_runtime_window(controller)

    assert summary["violation_count"] == 1
    assert summary["violations"][0]["key"] == "position_size"
    assert controller.session_state.current_params["position_size"] == 0.2
    assert controller.current_cycle_runtime_locked is False
    assert runtime.calls[0]["position_size"] == 0.2
    assert runtime.calls[-1]["position_size"] == 0.2


def test_apply_safety_override_updates_locked_runtime_without_violation():
    controller, _runtime = _build_controller({"position_size": 0.2, "kill_switch": False})
    begin_cycle_runtime_window(controller, cycle_id=8)

    applied = apply_safety_override(
        controller,
        {"kill_switch": True},
        source="manual_guard",
    )

    assert applied == {"kill_switch": True}
    assert resolve_active_runtime_params(controller)["kill_switch"] is True
    assert resolve_effective_runtime_params(controller)["kill_switch"] is True

    summary = finalize_cycle_runtime_window(controller)
    assert summary["violation_count"] == 0
    assert summary["safety_override_keys"] == ["kill_switch"]
    assert controller.session_state.current_params["kill_switch"] is True


def test_apply_safety_override_rejects_non_safety_key():
    controller, _runtime = _build_controller({"position_size": 0.2})
    begin_cycle_runtime_window(controller, cycle_id=9)

    with pytest.raises(ValueError):
        apply_safety_override(
            controller,
            {"position_size": 0.12},
            source="invalid_override",
        )


def test_regime_runtime_profile_applies_overlay_and_entry_threshold():
    controller, _runtime = _build_controller(
        {"position_size": 0.18, "cash_reserve": 0.2, "signal_threshold": 0.55}
    )
    controller.regime_controls = {
        "bear": {
            "position_size": 0.09,
            "cash_reserve": 0.41,
            "signal_threshold": 0.66,
            "max_positions": 2,
        }
    }
    controller.risk_policy = {"clamps": {"position_size": {"min": 0.05, "max": 0.30}}}
    controller.review_policy = {
        "param_clamps": {
            "cash_reserve": {"min": 0.0, "max": 0.8},
            "signal_threshold": {"min": 0.30, "max": 0.95},
        }
    }
    begin_cycle_runtime_window(controller, cycle_id=10)

    profile = build_regime_runtime_profile(controller, regime="bear")
    assert profile["regime"] == "bear"
    assert profile["applied"] is True
    assert profile["entry_threshold"] == {"key": "signal_threshold", "value": 0.66}

    effective = apply_regime_runtime_profile(controller, profile)
    assert effective["position_size"] == 0.09
    assert resolve_effective_runtime_params(controller)["position_size"] == 0.09


def test_resolve_entry_threshold_spec_prefers_first_supported_key():
    spec = resolve_entry_threshold_spec(
        {
            "min_value_quality_score": 0.52,
            "signal_threshold": 0.70,
        }
    )
    assert spec == {"key": "signal_threshold", "value": 0.7}
