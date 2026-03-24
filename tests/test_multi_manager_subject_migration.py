import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from invest_evolution.application.train import TrainingResult
from invest_evolution.application.training.execution import (
    ManagerExecutionBundle,
    ManagerExecutionService,
)
from invest_evolution.application.training.persistence import TrainingPersistenceService
from invest_evolution.application.training.review_contracts import (
    ReviewStageEnvelope,
    SimulationStageEnvelope,
    build_cycle_run_context,
)
from invest_evolution.application.training.controller import TrainingLifecycleService
from invest_evolution.application.training.observability import TrainingObservabilityService
from invest_evolution.application.training.execution import TrainingOutcomeService
from invest_evolution.application.training.review import TrainingReviewService
from invest_evolution.application.training.review import TrainingReviewStageService
from invest_evolution.investment.contracts import (
    AllocationReviewReport,
    ManagerAttribution,
    ManagerPlan,
    ManagerPlanPosition,
    ManagerResult,
    ManagerReviewReport,
    ManagerRunContext,
    PortfolioPlan,
    PortfolioPlanPosition,
)
from tests.test_manager_execution_services import FakeRegistry


def _build_manager_bundle(*, include_empty_manager: bool = False) -> ManagerExecutionBundle:
    momentum_plan = ManagerPlan(
        manager_id="momentum",
        manager_name="Momentum Manager",
        as_of_date="20260318",
        regime="bull",
        positions=[
            ManagerPlanPosition(
                code="sh.600519",
                rank=1,
                target_weight=0.32,
                score=0.91,
                thesis="trend continuation",
            ),
            ManagerPlanPosition(
                code="sh.600036",
                rank=2,
                target_weight=0.18,
                score=0.78,
                thesis="relative strength",
            ),
        ],
        cash_reserve=0.18,
        max_positions=2,
        budget_weight=0.6,
        confidence=0.83,
        reasoning="momentum sleeve remains constructive",
        source_manager_id="momentum",
        source_manager_config_ref="configs/momentum.yaml",
    )
    value_plan = ManagerPlan(
        manager_id="value_quality",
        manager_name="Value Quality Manager",
        as_of_date="20260318",
        regime="bull",
        positions=[]
        if include_empty_manager
        else [
            ManagerPlanPosition(
                code="sh.601318",
                rank=1,
                target_weight=0.22,
                score=0.74,
                thesis="quality compounder",
            )
        ],
        cash_reserve=0.45 if include_empty_manager else 0.28,
        max_positions=1,
        budget_weight=0.4,
        confidence=0.49 if include_empty_manager else 0.71,
        reasoning="quality sleeve is selective",
        source_manager_id="value_quality",
        source_manager_config_ref="configs/value.yaml",
    )
    manager_results = [
        ManagerResult(
            manager_id="momentum",
            as_of_date="20260318",
            status="planned",
            plan=momentum_plan,
            metrics={"position_count": 2},
            attribution=ManagerAttribution(
                manager_id="momentum",
                selected_codes=["sh.600519", "sh.600036"],
                gross_budget_weight=0.6,
                active_exposure=0.5,
                code_contributions={"sh.600519": 0.192, "sh.600036": 0.108},
            ),
        ),
        ManagerResult(
            manager_id="value_quality",
            as_of_date="20260318",
            status="empty" if include_empty_manager else "planned",
            plan=value_plan,
            metrics={"position_count": 0 if include_empty_manager else 1},
            attribution=ManagerAttribution(
                manager_id="value_quality",
                selected_codes=[] if include_empty_manager else ["sh.601318"],
                gross_budget_weight=0.4,
                active_exposure=0.0 if include_empty_manager else 0.22,
                code_contributions={} if include_empty_manager else {"sh.601318": 0.088},
            ),
        ),
    ]
    portfolio_positions = [
        PortfolioPlanPosition(
            code="sh.600519",
            target_weight=0.32,
            rank=1,
            source_managers=["momentum"],
            manager_weights={"momentum": 1.0},
            thesis="trend continuation",
        ),
        PortfolioPlanPosition(
            code="sh.600036",
            target_weight=0.18,
            rank=2,
            source_managers=["momentum"],
            manager_weights={"momentum": 1.0},
            thesis="relative strength",
        ),
    ]
    if not include_empty_manager:
        portfolio_positions.append(
            PortfolioPlanPosition(
                code="sh.601318",
                target_weight=0.22,
                rank=3,
                source_managers=["value_quality"],
                manager_weights={"value_quality": 1.0},
                thesis="quality compounder",
            )
        )
    portfolio_plan = PortfolioPlan(
        as_of_date="20260318",
        regime="bull",
        positions=portfolio_positions,
        cash_reserve=0.28 if not include_empty_manager else 0.5,
        active_manager_ids=["momentum", "value_quality"],
        manager_weights={"momentum": 0.6, "value_quality": 0.4},
        confidence=0.81,
        reasoning="portfolio assembled from manager sleeves",
        metadata={"assembly_mode": "portfolio_assembler"},
    )
    return ManagerExecutionBundle(
        run_context=ManagerRunContext(
            as_of_date="20260318",
            regime="bull",
            budget_weights={"momentum": 0.6, "value_quality": 0.4},
            runtime_params={"position_size": 0.12},
            active_manager_ids=["momentum", "value_quality"],
            governance_context={"regime": "bull"},
        ),
        manager_results=manager_results,
        portfolio_plan=portfolio_plan,
        dominant_manager_id="momentum",
        manager_outputs={
            "momentum": SimpleNamespace(manager_id="momentum", manager_config_ref="configs/momentum.yaml"),
            "value_quality": SimpleNamespace(
                manager_id="value_quality",
                manager_config_ref="configs/value.yaml",
            ),
        },
        execution_payload={"trading_plan": asdict(portfolio_plan.to_trading_plan())},
    )


def test_manager_execution_service_respects_allocator_and_assembly_flags():
    service = ManagerExecutionService(registry=FakeRegistry())

    class DummyController:
        manager_active_ids = ["momentum", "value_quality"]
        manager_budget_weights = {"momentum": 0.9, "value_quality": 0.1}
        manager_allocator_enabled = False
        portfolio_assembly_enabled = False
        current_params = {"position_size": 0.15}
        last_governance_decision = {
            "regime": "bull",
            "evidence": {"market_observation": {"stats": {"market_breadth": 0.64}}},
        }

    bundle = service.execute_manager_selection(
        DummyController(),
        cycle_id=1,
        cutoff_date="20260318",
        stock_data={},
    )

    assert bundle.run_context.budget_weights == {"momentum": 0.5, "value_quality": 0.5}
    assert bundle.dominant_manager_id == "momentum"
    assert bundle.portfolio_plan.active_manager_ids == ["momentum"]
    assert bundle.portfolio_plan.manager_weights == {"momentum": 1.0}
    assert bundle.execution_payload["trading_plan"]["source"] == "manager_runtime"


def test_build_cycle_run_context_carries_manager_portfolio_subjects():
    bundle = _build_manager_bundle()
    controller = SimpleNamespace(
        default_manager_id="momentum",
        default_manager_config_ref="configs/active.yaml",
        current_params={"position_size": 0.12},
        cycle_history=[SimpleNamespace(cycle_id=2), SimpleNamespace(cycle_id=3)],
        experiment_review_window={"mode": "rolling", "size": 2},
        experiment_promotion_policy={},
        experiment_protocol={"protocol": {"shadow_mode": True}},
    )

    context = cast(
        dict[str, Any],
        build_cycle_run_context(
            controller,
            cycle_id=4,
            manager_output=bundle.manager_outputs["momentum"],
            execution_snapshot={
                "basis_stage": "pre_review",
                "active_runtime_config_ref": "configs/active.yaml",
                "manager_config_ref": "configs/active.yaml",
                "execution_defaults": {
                    "default_manager_id": bundle.dominant_manager_id,
                    "default_manager_config_ref": "configs/active.yaml",
                },
                "runtime_overrides": {"position_size": 0.1},
                "subject_type": "manager_portfolio",
                "dominant_manager_id": bundle.dominant_manager_id,
                "manager_results": [item.to_dict() for item in bundle.manager_results],
                "portfolio_plan": bundle.portfolio_plan.to_dict(),
                "compatibility_fields": {
                    "derived": True,
                    "source": "dominant_manager",
                    "manager_id": bundle.dominant_manager_id,
                },
            },
            evaluation_context={
                "portfolio_attribution": {"sh.600519": 0.32},
                "manager_review_report": {
                    "summary": {
                        "manager_count": 1,
                        "active_manager_ids": ["momentum"],
                        "verdict_counts": {},
                    }
                },
                "allocation_review_report": {"verdict": "continue"},
            },
        ),
    )

    assert context["subject_type"] == "manager_portfolio"
    assert context["dominant_manager_id"] == "momentum"
    assert context["portfolio_plan"]["active_manager_ids"] == ["momentum", "value_quality"]
    assert len(context["manager_results"]) == 2
    assert context["portfolio_attribution"]["sh.600519"] == 0.32
    assert context["manager_review_report"]["summary"]["active_manager_ids"] == ["momentum"]
    assert context["compatibility_fields"]["derived"] is True
    assert context["shadow_mode"] is True


def test_training_review_stage_service_runs_dual_review_for_manager_subjects():
    service = TrainingReviewStageService()
    bundle = _build_manager_bundle(include_empty_manager=True)
    saved_reviews = []

    class DummyModel:
        def __init__(self):
            self.updates = []

        def update_runtime_overrides(self, updates):
            self.updates.append(dict(updates))

    class DummyController:
        manager_arch_enabled = True
        dual_review_enabled = True
        manager_shadow_mode = False
        manager_allocator_enabled = True
        current_params = {"position_size": 0.12}
        manager_budget_weights = {"momentum": 0.6, "value_quality": 0.4}
        selection_agent_weights = {"trend_hunter": 1.0, "contrarian": 1.0}
        manager_runtime = DummyModel()
        training_review_service = TrainingReviewService()
        training_manager_review_stage_service = None
        training_allocation_review_stage_service = None
        cycle_records = []
        cycle_history = []
        experiment_review_window = {"mode": "rolling", "size": 2}
        artifact_recorder = SimpleNamespace(
            save_manager_review_artifact=lambda report, cycle_id: saved_reviews.append(
                ("manager_review", report, cycle_id)
            ),
            save_allocation_review_artifact=lambda report, cycle_id: saved_reviews.append(
                ("allocation_review", report, cycle_id)
            ),
        )
        agent_tracker = SimpleNamespace(compute_accuracy=lambda last_n_cycles=20: {"accuracy": 0.8})
        training_manager_review_stage_service: object | None = None
        training_allocation_review_stage_service: object | None = None

        @staticmethod
        def _emit_agent_status(*args, **kwargs):
            del args, kwargs

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            del args, kwargs

    from invest_evolution.application.training.review import AllocationReviewStageService
    from invest_evolution.application.training.review import ManagerReviewStageService

    controller = DummyController()
    controller.training_manager_review_stage_service = ManagerReviewStageService()
    controller.training_allocation_review_stage_service = AllocationReviewStageService()

    cycle_payload = {"benchmark_passed": True}
    simulation_envelope = SimulationStageEnvelope.from_cycle_payload(cycle_payload)

    result = service.run_review_stage(
        controller,
        cycle_id=7,
        cutoff_date="20260318",
        sim_result=SimpleNamespace(return_pct=1.8, total_pnl=1800.0, total_trades=3, win_rate=0.67),
        regime_result={"regime": "bull"},
        selected=["sh.600519", "sh.600036"],
        cycle_payload=cycle_payload,
        trade_dicts=[{"action": "SELL", "amount": 10000.0}],
        requested_data_mode="offline",
        effective_data_mode="offline",
        llm_mode="dry_run",
        degraded=False,
        degrade_reason="",
        data_mode="offline",
        selection_mode="manager_portfolio",
        agent_used=False,
        llm_used=False,
        manager_output=bundle.manager_outputs["momentum"],
        research_feedback={"recommendation": {"bias": "maintain"}},
        optimization_event_factory=SimpleNamespace,
        simulation_envelope=simulation_envelope,
        manager_bundle=bundle,
    )

    assert result.review_applied is True
    assert result.manager_review_report["subject_type"] == "manager_review"
    assert result.manager_review_report["summary"]["manager_count"] == 2
    assert result.manager_review_report["summary"]["active_manager_ids"] == [
        "momentum",
        "value_quality",
    ]
    assert result.allocation_review_report["subject_type"] == "allocation_review"
    assert result.allocation_review_report["active_manager_ids"] == ["momentum", "value_quality"]
    assert (
        dict(result.review_decision.get("manager_budget_adjustments") or {}).get("momentum", 0.0)
        > 0.6
    )
    assert result.review_trace["decision_source"] == "dual_review"
    assert controller.manager_budget_weights["momentum"] > controller.manager_budget_weights["value_quality"]
    assert controller.manager_runtime.updates[-1] == dict(
        result.review_decision.get("param_adjustments") or {}
    )
    assert controller.manager_runtime.updates[-1]["position_size"] < 0.12
    assert saved_reviews[0][2] == 7


def test_training_outcome_and_persistence_write_manager_portfolio_fields(tmp_path):
    service = TrainingOutcomeService()
    bundle = _build_manager_bundle()
    manager_review_report = {
        "subject_type": "manager_review",
        "reports": [
            ManagerReviewReport(
                manager_id="momentum",
                as_of_date="20260318",
                verdict="continue",
                findings=["healthy sleeve"],
            ).to_dict()
        ],
        "summary": {"manager_count": 2},
    }
    allocation_review_report = {
        **AllocationReviewReport(
            as_of_date="20260318",
            regime="bull",
            verdict="continue",
            active_manager_ids=["momentum", "value_quality"],
            allocation_weights={"momentum": 0.6, "value_quality": 0.4},
        ).to_dict(),
        "subject_type": "allocation_review",
    }

    class DummyController:
        default_manager_id = "momentum"
        default_manager_config_ref = "configs/active.yaml"
        governance_enabled = True
        governance_mode = "rule"
        last_governance_decision = {
            "dominant_manager_id": "momentum",
            "active_manager_ids": ["momentum"],
            "manager_budget_weights": {"momentum": 1.0},
            "regime": "bull",
        }
        current_params = {"position_size": 0.12}
        experiment_review_window = {"mode": "rolling", "size": 2}
        experiment_promotion_policy = {}
        cycle_history = [SimpleNamespace(cycle_id=3)]
        quality_gate_matrix = {}
        promotion_gate_policy = {}
        freeze_gate_policy = {}
        manager_persistence_enabled = True
        output_dir = tmp_path / "training"
        assessment_history = []
        last_allocation_plan = {}

    cycle_payload = {
        "strategy_scores": {"overall_score": 0.88},
        "analysis": "dual review healthy",
        "execution_snapshot": {
            "basis_stage": "pre_review",
            "cycle_id": 4,
            "active_runtime_config_ref": "configs/active.yaml",
            "manager_config_ref": "configs/active.yaml",
            "execution_defaults": {
                "default_manager_id": "momentum",
                "default_manager_config_ref": "configs/active.yaml",
            },
            "runtime_overrides": {"position_size": 0.12},
            "governance_decision": {
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "value_quality"],
                "regime": "bull",
            },
            "selection_mode": "manager_portfolio",
            "benchmark_passed": True,
            "subject_type": "manager_portfolio",
            "dominant_manager_id": "momentum",
            "manager_results": [item.to_dict() for item in bundle.manager_results],
            "portfolio_plan": bundle.portfolio_plan.to_dict(),
        },
    }
    simulation_envelope = SimulationStageEnvelope.from_cycle_payload(cycle_payload)
    review_envelope = ReviewStageEnvelope.from_structured_inputs(
        simulation=simulation_envelope,
        analysis="dual review healthy",
        review_decision={},
        stage_snapshots=simulation_envelope.stage_snapshots,
    )

    cycle_result = service.build_cycle_result(
        DummyController(),
        result_factory=TrainingResult,
        cycle_id=4,
        cutoff_date="20260318",
        selected=["sh.600519", "sh.600036", "sh.601318"],
        sim_result=SimpleNamespace(initial_capital=100000.0, final_value=103500.0, return_pct=3.5),
        is_profit=True,
        trade_dicts=[{"action": "SELL", "amount": 10000.0}],
        data_mode="offline",
        requested_data_mode="offline",
        effective_data_mode="offline",
        llm_mode="dry_run",
        degraded=False,
        degrade_reason="",
        selection_mode="manager_portfolio",
        agent_used=False,
        llm_used=False,
        benchmark_passed=True,
        cycle_payload=cycle_payload,
        simulation_envelope=simulation_envelope,
        review_envelope=review_envelope,
        review_applied=True,
        config_snapshot_path="snap.json",
        optimization_events=[],
        audit_tags={"subject_type": "manager_portfolio"},
        manager_output=bundle.manager_outputs["momentum"],
        research_feedback={"recommendation": {"bias": "maintain"}},
        manager_results=[item.to_dict() for item in bundle.manager_results],
        portfolio_plan=bundle.portfolio_plan.to_dict(),
        portfolio_attribution={"sh.600519": 0.32, "sh.600036": 0.18, "sh.601318": 0.22},
        manager_review_report=manager_review_report,
        allocation_review_report=allocation_review_report,
        dominant_manager_id="momentum",
    )

    assert cycle_result.manager_results[0]["manager_id"] == "momentum"
    assert cycle_result.portfolio_plan["active_manager_ids"] == ["momentum", "value_quality"]
    assert cycle_result.portfolio_attribution["sh.600519"] == 0.32
    assert cycle_result.governance_decision["dominant_manager_id"] == "momentum"
    assert cycle_result.execution_defaults == {
        "default_manager_id": "momentum",
        "default_manager_config_ref": "configs/active.yaml",
    }
    assert cycle_result.manager_review_report["summary"]["manager_count"] == 2
    assert cycle_result.allocation_review_report["subject_type"] == "allocation_review"
    assert cycle_result.run_context["subject_type"] == "manager_portfolio"
    assert cycle_result.compatibility_fields["derived"] is True

    persistence_service = TrainingPersistenceService()
    persistence_service.refresh_leaderboards = lambda controller: None
    persistence_service.save_cycle_result(DummyController(), cycle_result)

    payload = json.loads((tmp_path / "training" / "cycle_4.json").read_text(encoding="utf-8"))
    assert payload["portfolio_plan"]["active_manager_ids"] == ["momentum", "value_quality"]
    assert payload["manager_results"]["items"][1]["manager_id"] == "value_quality"
    assert payload["portfolio_attribution"]["sh.601318"] == 0.22
    assert payload["manager_review_report"]["summary"]["manager_count"] == 2
    assert payload["allocation_review_report"]["subject_type"] == "allocation_review"
    assert payload["compatibility_fields"]["derived"] is True


def test_training_lifecycle_and_observability_emit_manager_portfolio_semantics():
    lifecycle_service = TrainingLifecycleService()
    observability_service = TrainingObservabilityService()
    emitted = []
    logs = []

    class DummyController:
        manager_arch_enabled = True
        dual_review_enabled = True
        cycle_history = []
        current_cycle_id = 0
        last_governance_decision = {
            "dominant_manager_id": "momentum",
            "active_manager_ids": ["momentum"],
            "manager_budget_weights": {"momentum": 1.0},
            "regime": "bull",
        }
        last_feedback_optimization = {}
        last_cycle_meta = {}
        default_manager_id = "momentum"
        on_cycle_complete = None
        training_persistence_service = SimpleNamespace(
            record_self_assessment=lambda owner, snapshot_factory, cycle_result, cycle_dict: None,
            save_cycle_result=lambda owner, result: None,
        )
        freeze_gate_service = SimpleNamespace(evaluate_freeze_gate=lambda owner: {"passed": False})

        @staticmethod
        def _research_feedback_brief(feedback):
            return {"sample_count": int((feedback or {}).get("sample_count") or 0)}

        @staticmethod
        def _emit_module_log(*args, **kwargs):
            logs.append((args, kwargs))

        @staticmethod
        def _emit_runtime_event(event_type, data):
            emitted.append((event_type, data))

        @staticmethod
        def _emit_agent_status(*args, **kwargs):
            emitted.append(("agent_status", {"args": args, "kwargs": kwargs}))

        @staticmethod
        def _emit_meeting_speech(*args, **kwargs):
            emitted.append(("meeting_speech", {"args": args, "kwargs": kwargs}))

        @staticmethod
        def _thinking_excerpt(text):
            return str(text)

    controller = DummyController()
    result = TrainingResult(
        cycle_id=5,
        cutoff_date="20260318",
        selected_stocks=["sh.600519", "sh.600036"],
        initial_capital=100000.0,
        final_value=103200.0,
        return_pct=3.2,
        is_profit=True,
        trade_history=[{"action": "SELL"}],
        params={"position_size": 0.12},
        selection_mode="manager_portfolio",
        manager_results=[{"manager_id": "momentum"}, {"manager_id": "value_quality"}],
        portfolio_plan={
            "active_manager_ids": ["momentum", "value_quality"],
            "positions": [{"code": "sh.600519"}, {"code": "sh.600036"}],
        },
        portfolio_attribution={"sh.600519": 0.32},
        manager_review_report={"summary": {"manager_count": 2}},
        allocation_review_report={"verdict": "continue"},
        dominant_manager_id="momentum",
        execution_defaults={
            "default_manager_id": "momentum",
            "default_manager_config_ref": "configs/active.yaml",
        },
        compatibility_fields={"derived": True, "source": "dominant_manager", "manager_id": "momentum"},
    )

    lifecycle_service.finalize_cycle(
        controller,
        cycle_result=result,
        assessment_payload={
            "regime": "bull",
            "plan_source": "manager_portfolio",
            "benchmark_passed": True,
        },
        cycle_id=5,
        cutoff_date="20260318",
        sim_result=SimpleNamespace(return_pct=3.2, final_value=103200.0),
        is_profit=True,
        selected=["sh.600519", "sh.600036"],
        trade_dicts=[{"action": "SELL"}],
        review_applied=False,
        selection_mode="manager_portfolio",
        requested_data_mode="offline",
        effective_data_mode="offline",
        llm_mode="dry_run",
        degraded=False,
        degrade_reason="",
        research_feedback={"sample_count": 4},
    )

    observability_service.handle_selection_progress(controller, {"message": "portfolio assembled"})
    observability_service.handle_review_progress(controller, {"message": "dual review completed"})

    assert controller.last_cycle_meta["subject_type"] == "manager_portfolio"
    assert controller.last_cycle_meta["active_manager_ids"] == ["momentum", "value_quality"]
    assert emitted[0][0] == "cycle_complete"
    assert emitted[0][1]["subject_type"] == "manager_portfolio"
    assert emitted[0][1]["dominant_manager_id"] == "momentum"
    assert emitted[1][1]["kwargs"]["stage"] == "selection"
    assert emitted[1][1]["args"][0] == "ManagerSelection"
    assert emitted[2][1]["kwargs"]["stage"] == "review"
    assert emitted[2][1]["args"][0] == "DualReview"


def test_docs_describe_multi_manager_default_flow():
    root = Path(__file__).resolve().parents[1]
    main_flow = (root / "docs" / "MAIN_FLOW.md").read_text(encoding="utf-8")
    training_flow = (root / "docs" / "TRAINING_FLOW.md").read_text(encoding="utf-8")
    interaction_flow = (root / "docs" / "AGENT_INTERACTION.md").read_text(encoding="utf-8")

    assert "ManagerExecutionService" in main_flow
    assert "PortfolioPlan" in main_flow
    assert "ManagerReviewStageService" in training_flow
    assert "AllocationReviewStageService" in training_flow
    assert "ManagerAgent" in interaction_flow
    assert "Capability Hub" in interaction_flow
    assert "participant SEL as SelectionMeeting" not in training_flow
    assert "participant REVIEW as ReviewMeeting" not in training_flow
