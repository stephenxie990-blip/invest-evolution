from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from invest_evolution.application.training.controller import TrainingSessionState, session_last_governance_decision
from invest_evolution.application.training.execution import ManagerExecutionService
from invest_evolution.application.training.policy import (
    TrainingGovernanceService,
    resolve_training_scope,
)
from invest_evolution.investment.managers import resolve_manager_config_ref as resolve_registry_manager_config_ref


def _abs_ref(ref: str) -> str:
    text = str(ref or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve(strict=False))


def test_resolve_training_scope_canonicalizes_active_and_default_config_refs():
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref=_abs_ref(
                resolve_registry_manager_config_ref("momentum")
            ),
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "defensive_low_vol"],
                "manager_budget_weights": {"momentum": 0.6, "defensive_low_vol": 0.4},
                "regime": "bull",
            },
        )
    )

    scope = resolve_training_scope(
        controller=controller,
        governance_decision={
            "dominant_manager_id": "momentum",
            "active_manager_ids": ["momentum", "defensive_low_vol"],
            "manager_budget_weights": {"momentum": 0.6, "defensive_low_vol": 0.4},
            "regime": "bull",
        },
        execution_snapshot={
            # Simulate a stale or alias config ref leaking into the snapshot.
            "active_runtime_config_ref": "defensive_low_vol_v1",
            "manager_config_ref": "defensive_low_vol_v1",
            "execution_defaults": {
                "default_manager_id": "momentum",
                "default_manager_config_ref": "defensive_low_vol_v1",
            },
        },
    )

    assert scope.dominant_manager_id == "momentum"
    assert scope.manager_config_ref.endswith("configs/momentum_v1.yaml")
    assert scope.active_runtime_config_ref == scope.manager_config_ref
    assert scope.execution_defaults == {
        "default_manager_id": "momentum",
        "default_manager_config_ref": scope.manager_config_ref,
    }


def test_apply_governance_clamps_to_single_manager_when_manager_arch_disabled(monkeypatch, tmp_path):
    service = TrainingGovernanceService()

    canonical_momentum = _abs_ref(resolve_registry_manager_config_ref("momentum"))

    class FakeDecision:
        regime = "bull"
        regime_confidence = 0.7
        decision_confidence = 0.8
        decision_source = "rule"
        regime_source = "rule"
        cash_reserve_hint = 0.05
        portfolio_constraints = {}
        reasoning = "fake decision"
        guardrail_checks = []
        evidence = {"rule_result": {"reasoning": "fake"}}
        metadata = {}

        allocation_plan = {
            "selected_manager_config_refs": {
                "momentum": canonical_momentum,
                "value_quality": _abs_ref(resolve_registry_manager_config_ref("value_quality")),
            }
        }

        def to_dict(self):
            return {
                "regime": self.regime,
                "regime_confidence": self.regime_confidence,
                "decision_confidence": self.decision_confidence,
                "decision_source": self.decision_source,
                "regime_source": self.regime_source,
                "active_manager_ids": ["momentum", "value_quality"],
                "manager_budget_weights": {"momentum": 0.6, "value_quality": 0.4},
                "dominant_manager_id": "momentum",
                "allocation_plan": dict(self.allocation_plan),
                "metadata": dict(self.metadata),
            }

    monkeypatch.setattr(service, "decide_governance", lambda *a, **k: FakeDecision())

    controller = SimpleNamespace(
        governance_enabled=True,
        governance_mode="rule",
        manager_arch_enabled=False,
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref=canonical_momentum,
            last_governance_decision={},
        ),
        governance_history=[],
        last_allocation_plan={},
        manager_active_ids=[],
        experiment_allowed_manager_ids=[],
        governance_allowed_manager_ids=[],
        data_manager=SimpleNamespace(),
        output_dir=str(tmp_path),
        current_cycle_id=0,
        _event_context=lambda cycle_id: {"cycle_id": cycle_id},
        _thinking_excerpt=lambda text: "",
        _sync_runtime_policy_from_manager_runtime=lambda: None,
        _emit_agent_status=lambda *a, **k: None,
        _emit_module_log=lambda *a, **k: None,
    )

    events: list[tuple[str, dict]] = []
    service.apply_governance(
        controller,
        stock_data={"sh.600519": {"rows": 1}},
        cutoff_date="20240201",
        cycle_id=1,
        event_emitter=lambda name, payload: events.append((name, payload)),
    )

    decision_payload = session_last_governance_decision(controller)
    assert decision_payload["dominant_manager_id"] == "momentum"
    assert decision_payload["active_manager_ids"] == ["momentum"]
    assert decision_payload["manager_budget_weights"] == {"momentum": 1.0}
    assert dict(decision_payload.get("metadata") or {}).get("subject_type") == "single_manager"
    assert dict(decision_payload.get("metadata") or {}).get("clamped") is True

    assert controller.manager_active_ids == ["momentum"]
    assert controller.portfolio_assembly_enabled is False
    assert controller.session_state.default_manager_id == "momentum"
    assert controller.session_state.default_manager_config_ref.endswith("configs/momentum_v1.yaml")


def test_manager_execution_service_build_run_context_isolated_when_arch_disabled():
    controller = SimpleNamespace(
        manager_arch_enabled=False,
        session_state=TrainingSessionState(
            default_manager_id="momentum",
            default_manager_config_ref=_abs_ref(resolve_registry_manager_config_ref("momentum")),
            current_params={},
            last_governance_decision={
                "dominant_manager_id": "value_quality",
                "active_manager_ids": ["momentum", "value_quality", "defensive_low_vol"],
                "manager_budget_weights": {"momentum": 0.4, "value_quality": 0.4, "defensive_low_vol": 0.2},
                "regime": "bear",
            },
        ),
    )

    service = ManagerExecutionService()
    run_context = service.build_run_context(
        controller,
        cutoff_date="20240201",
        stock_data={"sh.600519": {"rows": 1}},
    )

    assert run_context.active_manager_ids == ["value_quality"]
    assert run_context.budget_weights == {"value_quality": 1.0}
    assert dict(run_context.metadata or {}).get("subject_type") == "single_manager"
    assert dict(run_context.metadata or {}).get("dominant_manager_id") == "value_quality"
