import json
from types import SimpleNamespace
from typing import Any, cast

from invest_evolution.application.train import OptimizationEvent, SelfLearningController, TrainingResult
import invest_evolution.application.training.execution as execution_services_module
from invest_evolution.application.training.controller import TrainingExecutionService
from invest_evolution.application.training.controller import (
    OutcomeStageContext,
    ReviewStageContext,
    SelectionStageContext,
    SimulationStageContext,
)
from invest_evolution.application.training.review_contracts import (
    ReviewStageEnvelope,
    SimulationStageEnvelope,
)
from invest_evolution.application.training.review import TrainingReviewStageResult
from invest_evolution.application.training.controller import TrainingSessionState
from invest_evolution.application.training.execution import TrainingSelectionResult


def _make_controller(tmp_path):
    controller = SelfLearningController(
        output_dir=str(tmp_path / "training"),
        artifact_log_dir=str(tmp_path / "artifacts"),
        config_audit_log_path=str(tmp_path / "audit" / "changes.jsonl"),
        config_snapshot_dir=str(tmp_path / "snapshots"),
    )
    controller.default_manager_id = "momentum"
    controller.default_manager_config_ref = "configs/active.yaml"
    controller.current_params = {"position_size": 0.12}
    controller.experiment_protocol = {"protocol": {"shadow_mode": True}}
    controller.experiment_simulation_days = 2
    controller.last_governance_decision = {
        "dominant_manager_id": "momentum",
        "active_manager_ids": ["momentum"],
        "manager_budget_weights": {"momentum": 1.0},
        "regime": "bull",
        "decision_source": "test",
    }
    return cast(Any, controller)


def test_training_validation_flow_runs_end_to_end_and_persists_reports(monkeypatch, tmp_path):
    service = TrainingExecutionService()
    controller = _make_controller(tmp_path)
    controller.default_manager_id = "defensive"
    controller.default_manager_config_ref = "configs/defensive.yaml"
    candidate_ref = tmp_path / "candidate.generated.yaml"
    ab_requests = []

    controller.cycle_history = [
        SimpleNamespace(
            cycle_id=1,
            manager_id="momentum_peer",
            governance_decision={
                "dominant_manager_id": "momentum_peer",
                "active_manager_ids": ["momentum_peer"],
                "manager_budget_weights": {"momentum_peer": 1.0},
                "regime": "bull",
            },
            audit_tags={"governance_regime": "bull"},
            strategy_scores={"overall_score": 0.8},
            return_pct=1.2,
            benchmark_passed=True,
            lineage_record={"deployment_stage": "active"},
        )
    ]
    trading_plan = SimpleNamespace(
        positions=[SimpleNamespace(code="sh.600000")],
        source="portfolio_assembler",
        max_positions=1,
    )
    portfolio_plan = SimpleNamespace(
        active_manager_ids=["momentum"],
        confidence=0.84,
        reasoning="single manager dominated this cycle",
        cash_reserve=0.15,
        positions=[SimpleNamespace(code="sh.600000", target_weight=0.85)],
        to_dict=lambda: {
            "active_manager_ids": ["momentum"],
            "manager_weights": {"momentum": 1.0},
            "positions": [{"code": "sh.600000"}],
            "cash_reserve": 0.15,
            "confidence": 0.84,
            "reasoning": "single manager dominated this cycle",
        },
        to_trading_plan=lambda: trading_plan,
    )
    dominant_output = SimpleNamespace(manager_id="momentum", manager_config_ref="configs/active.yaml")
    manager_bundle = SimpleNamespace(
        portfolio_plan=portfolio_plan,
        manager_results=[
            SimpleNamespace(
                to_dict=lambda: {
                    "manager_id": "momentum",
                    "status": "planned",
                    "plan": {"selected_codes": ["sh.600000"]},
                }
            )
        ],
        dominant_manager_id="momentum",
        manager_outputs={"momentum": dominant_output},
    )

    monkeypatch.setattr(
        controller.training_selection_service,
        "run_selection_stage",
        lambda owner, **kwargs: TrainingSelectionResult(
            regime_result={"regime": "bull"},
            selected_codes=["sh.600000"],
            selected_data={"sh.600000": [{"close": 10.0}, {"close": 10.8}]},
            selection_mode="manager_portfolio",
            agent_used=False,
            manager_bundle=manager_bundle,
            manager_results=[item.to_dict() for item in manager_bundle.manager_results],
            portfolio_plan=portfolio_plan.to_dict(),
            dominant_manager_id="momentum",
            selection_trace={
                "selected": ["sh.600000"],
                "active_managers": ["momentum"],
                "dominant_manager_id": "momentum",
                "portfolio_plan": portfolio_plan.to_dict(),
                "manager_results": [item.to_dict() for item in manager_bundle.manager_results],
                "decision_source": "manager_runtime",
            },
            compatibility_fields={
                "derived": True,
                "source": "dominant_manager",
                "field_role": "derived_compatibility",
                "manager_id": "momentum",
                "manager_config_ref": "configs/active.yaml",
            },
        ),
    )
    monkeypatch.setattr(controller, "_maybe_apply_allocator", lambda *args, **kwargs: None)

    trader = SimpleNamespace(
        initial_capital=100000.0,
        set_market_index_data=lambda frame: None,
        run_simulation=lambda start, dates: SimpleNamespace(
            initial_capital=100000.0,
            final_value=104500.0,
            return_pct=4.5,
            total_trades=2,
            win_rate=1.0,
            total_pnl=4500.0,
            per_stock_pnl={"sh.600000": 4500.0},
            daily_records=[
                {"total_value": 100000.0},
                {"total_value": 104500.0},
            ],
            winning_trades=2,
            losing_trades=0,
        ),
    )
    monkeypatch.setattr(controller.training_simulation_service, "build_trader", lambda *args, **kwargs: trader)
    monkeypatch.setattr(
        controller.training_simulation_service,
        "resolve_trading_dates",
        lambda **kwargs: ["20240201", "20240202"],
    )
    monkeypatch.setattr(
        controller.training_simulation_service,
        "build_benchmark_context",
        lambda *args, **kwargs: ([100000.0, 101000.0], None),
    )
    monkeypatch.setattr(
        controller.training_simulation_service,
        "build_cycle_payload_projection",
        lambda **kwargs: {
            "cycle_id": kwargs["cycle_id"],
            "regime": "bull",
            "plan_source": "meeting",
            "benchmark_passed": True,
            "regime_summary": {"sample_count": 8, "dominant_regime_share": 0.5},
            "strategy_scores": {
                "overall_score": 0.91,
                "signal_accuracy": 0.81,
                "timing_score": 0.79,
                "risk_control_score": 0.88,
            },
        },
    )
    monkeypatch.setattr(
        controller.training_simulation_service,
        "build_trade_dicts",
        lambda result: [{"action": "SELL", "amount": 12000.0, "turnover_rate": 1.1, "holding_days": 5}],
    )
    monkeypatch.setattr(
        controller.training_simulation_service,
        "evaluate_cycle_summary",
        lambda *args, **kwargs: {
            "benchmark_passed": True,
            "benchmark_strict_passed": True,
            "sharpe_ratio": 1.2,
            "max_drawdown": -0.05,
            "excess_return": 0.08,
            "benchmark_return": 0.03,
            "benchmark_source": "index_bar:sh.000300",
            "strategy_scores": {
                "overall_score": 0.91,
                "signal_accuracy": 0.81,
                "timing_score": 0.79,
                "risk_control_score": 0.88,
            },
        },
    )

    monkeypatch.setattr(
        controller.training_research_service,
        "persist_cycle_research_artifacts",
        lambda *args, **kwargs: {"saved_case_count": 1, "saved_attribution_count": 1},
    )
    monkeypatch.setattr(
        controller,
        "_load_research_feedback",
        lambda **kwargs: {
            "sample_count": 8,
            "recommendation": {"bias": "maintain", "summary": "feedback:maintain"},
            "horizons": {
                "T+20": {
                    "hit_rate": 0.6,
                    "invalidation_rate": 0.2,
                    "interval_hit_rate": 0.5,
                }
            },
            "scope": {
                "effective_scope": "regime",
                "regime_sample_count": 8,
                "overall_sample_count": 12,
            },
        },
    )
    monkeypatch.setattr(controller, "_build_feedback_optimization_plan", lambda *args, **kwargs: {})
    monkeypatch.setattr(controller, "_feedback_optimization_brief", lambda *args, **kwargs: {})

    review_event = OptimizationEvent(cycle_id=5, trigger="dual_review", stage="review_decision")
    monkeypatch.setattr(
        controller.training_review_stage_service,
        "run_review_stage",
        lambda *args, **kwargs: TrainingReviewStageResult(
            eval_report=SimpleNamespace(return_pct=4.5, benchmark_passed=True),
            review_decision={
                "reasoning": "cycle looks healthy",
                "causal_diagnosis": {"primary_driver": "trend_following"},
                "similarity_summary": {"matched_cycle_ids": [1]},
                "similar_results": [{"cycle_id": 1}],
            },
            review_applied=False,
            review_event=review_event,
        ),
    )
    monkeypatch.setattr(
        controller.training_ab_service,
        "run_candidate_ab_comparison",
        lambda *args, **kwargs: ab_requests.append(dict(kwargs)) or {
            "comparison": {
                "candidate_present": True,
                "comparable": True,
                "winner": "candidate",
                "candidate_outperformed": True,
                "return_lift_pct": 2.3,
                "strategy_score_lift": 0.11,
                "benchmark_lift": 0.2,
            }
        },
    )
    monkeypatch.setattr(
        controller.config_service,
        "write_runtime_snapshot",
        lambda **kwargs: tmp_path / "snapshots" / f"cycle_{kwargs['cycle_id']}.yaml",
    )
    monkeypatch.setattr(controller.freeze_gate_service, "evaluate_freeze_gate", lambda owner: {})
    monkeypatch.setattr(controller.training_persistence_service, "refresh_leaderboards", lambda owner: None)
    monkeypatch.setattr(controller, "_emit_agent_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(controller, "_emit_module_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(controller, "_emit_runtime_event", lambda *args, **kwargs: None)

    result = service.execute_loaded_cycle(
        controller,
        result_factory=TrainingResult,
        optimization_event_factory=OptimizationEvent,
        cycle_id=5,
        cutoff_date="20240201",
        stock_data={"sh.600000": [{"close": 10.0}, {"close": 10.8}]},
        diagnostics={"ready": True},
        requested_data_mode="offline",
        effective_data_mode="offline",
        llm_mode="dry_run",
        degraded=False,
        degrade_reason="",
        data_mode="offline",
        llm_used=False,
        optimization_events=[
            {
                "stage": "runtime_config_mutation",
                "runtime_config_mutation_payload": {
                    "runtime_config_ref": str(candidate_ref),
                    "auto_applied": False,
                },
                "decision": {
                    "runtime_config_ref": "stale_candidate.yaml",
                    "auto_applied": True,
                },
                "notes": "candidate runtime config generated",
            }
        ],
    )

    assert result is not None
    assert ab_requests[0]["manager_id"] == "momentum"
    assert result.run_context["candidate_runtime_config_ref"].endswith("candidate.generated.yaml")
    assert result.validation_report["shadow_mode"] is True
    assert result.validation_report["summary"]["status"] == "passed"
    assert result.validation_summary["status"] == "passed"
    assert result.peer_comparison_report["comparable"] is True
    assert result.peer_comparison_report["candidate_outperformed_peers"] is True
    assert result.judge_report["decision"] == "promote"
    assert result.judge_report["shadow_mode"] is True
    assert result.judge_report["actionable"] is False

    cycle_payload = json.loads((tmp_path / "training" / "cycle_5.json").read_text(encoding="utf-8"))
    validation_payload = json.loads(
        (tmp_path / "training" / "validation" / "cycle_5_validation.json").read_text(encoding="utf-8")
    )
    peer_payload = json.loads(
        (tmp_path / "training" / "validation" / "cycle_5_peer_comparison.json").read_text(
            encoding="utf-8"
        )
    )
    judge_payload = json.loads(
        (tmp_path / "training" / "validation" / "cycle_5_judge.json").read_text(encoding="utf-8")
    )

    assert cycle_payload["validation_summary"]["status"] == "passed"
    assert cycle_payload["judge_report"]["decision"] == "promote"
    assert cycle_payload["stage_snapshots"]["simulation"]["stage"] == "simulation"
    assert cycle_payload["stage_snapshots"]["review"]["stage"] == "review"
    assert cycle_payload["stage_snapshots"]["outcome"]["stage"] == "outcome"
    assert cycle_payload["stage_snapshots"]["validation"]["stage"] == "validation"
    assert cycle_payload["stage_snapshots"]["validation"]["validation_summary"]["status"] == "passed"
    assert cycle_payload["execution_snapshot"]["contract_stage_snapshots"]["simulation"]["stage"] == "simulation"
    assert cycle_payload["execution_snapshot"]["contract_stage_snapshots"]["validation"]["stage"] == "validation"
    assert cycle_payload["run_context"]["contract_stage_snapshots"]["outcome"]["stage"] == "outcome"
    assert validation_payload["summary"]["status"] == "passed"
    assert validation_payload["summary"]["shadow_mode"] is True
    assert peer_payload["candidate_outperformed_peers"] is True
    assert judge_payload["decision"] == "promote"
    assert judge_payload["actionable"] is False


def test_validation_stage_prefers_run_context_manager_projection(monkeypatch):
    captured = []
    monkeypatch.setattr(
        execution_services_module,
        "run_validation_orchestrator",
        lambda **kwargs: captured.append(dict(kwargs))
        or {
            "validation_task_id": "val_test",
            "market_tagging": {"primary_tag": "bull"},
            "summary": {"status": "passed", "validation_task_id": "val_test"},
            "failure_tagging": {},
        },
    )
    service = TrainingExecutionService()
    controller = cast(
        Any,
        SimpleNamespace(
            session_state=TrainingSessionState(
                default_manager_id="defensive",
                default_manager_config_ref="configs/defensive.yaml",
                cycle_history=[],
            ),
            promotion_gate_policy={},
            quality_gate_matrix={"promotion": {}},
        ),
    )
    validation_context = service._run_validation_stage(
        controller,
        cycle_id=9,
        optimization_events=[],
        selection_context=SelectionStageContext(
            selection_result=None,
            manager_output=None,
            regime_result={"regime": "bull"},
            trading_plan=None,
            selected=[],
            selected_data={},
            selection_mode="manager_portfolio",
            agent_used=False,
            manager_bundle=None,
            manager_results_payload=[],
            portfolio_plan_payload={},
            dominant_manager_id="momentum",
            portfolio_attribution_payload={},
            compatibility_fields={},
        ),
        simulation_context=SimulationStageContext(
            sim_result=None,
            is_profit=True,
            trade_dicts=[],
            benchmark_passed=True,
            cycle_payload={"regime_summary": {}},
            research_artifacts={},
            research_feedback={},
            simulation_envelope=SimulationStageEnvelope(
                cycle_id=9,
                cutoff_date="20240201",
                regime="bull",
                strategy_scores={},
                governance_decision={},
                execution_snapshot={},
            ),
        ),
        review_context=ReviewStageContext(
            review_stage_result=None,
            review_decision={},
            review_applied=False,
            review_envelope=ReviewStageEnvelope(
                simulation=SimulationStageEnvelope(
                    cycle_id=9,
                    cutoff_date="20240201",
                    regime="bull",
                    execution_snapshot={},
                ),
            ),
            run_context={},
            ab_comparison={},
        ),
        outcome_context=OutcomeStageContext(
            cycle_result=SimpleNamespace(
                cycle_id=9,
                dominant_manager_id="",
                run_context={
                    "dominant_manager_id": "momentum",
                    "active_runtime_config_ref": "configs/active.yaml",
                    "manager_config_ref": "configs/active.yaml",
                    "execution_defaults": {
                        "default_manager_id": "momentum",
                        "default_manager_config_ref": "configs/active.yaml",
                    },
                },
                governance_decision={
                    "dominant_manager_id": "momentum",
                    "active_manager_ids": ["momentum"],
                    "manager_budget_weights": {"momentum": 1.0},
                    "regime": "bull",
                },
                ab_comparison={},
                research_feedback={},
                return_pct=4.2,
                benchmark_passed=True,
                strategy_scores={"overall_score": 0.88},
                portfolio_plan={},
                portfolio_attribution={},
                manager_results=[],
                manager_review_report={},
                allocation_review_report={},
            ),
            cycle_payload={},
        ),
    )

    assert captured[0]["manager_id"] == "momentum"
    assert dict(validation_context.validation_report.get("summary") or {})["status"] == "passed"


def test_attach_contract_stage_snapshots_keeps_stage_snapshots_canonical():
    cycle_result = SimpleNamespace(
        execution_snapshot={},
        run_context={},
        stage_snapshots={
            "validation": {
                "stage": "validation",
                "validation_summary": {"status": "passed"},
                "judge_report": {"decision": "promote"},
            }
        },
    )

    TrainingExecutionService._attach_contract_stage_snapshots(
        cycle_result,
        contract_stage_snapshots={
            "validation": {
                "stage": "validation",
                "validation_summary": {"status": "passed"},
                "judge_report": {},
            },
            "outcome": {"stage": "outcome", "promotion_record": {"status": "candidate_generated"}},
        },
    )

    assert cycle_result.execution_snapshot["contract_stage_snapshots"]["validation"]["judge_report"] == {}
    assert cycle_result.stage_snapshots["validation"]["judge_report"]["decision"] == "promote"
    assert cycle_result.stage_snapshots["outcome"]["promotion_record"]["status"] == "candidate_generated"


def test_apply_optimization_stage_keeps_feedback_state_out_of_cycle_payload(monkeypatch):
    service = TrainingExecutionService()
    triggered = []
    controller = cast(
        Any,
        SimpleNamespace(
            session_state=TrainingSessionState(consecutive_losses=3),
            max_losses_before_optimize=3,
            _build_feedback_optimization_plan=lambda *_args, **_kwargs: {"recommendation": {"bias": "defensive"}},
            _feedback_optimization_brief=lambda *_args, **kwargs: {"triggered": bool(kwargs.get("triggered"))},
            _trigger_optimization=lambda optimization_input, trade_dicts, **kwargs: triggered.append(
                {
                    "optimization_input": optimization_input,
                    "trade_dicts": list(trade_dicts),
                    "kwargs": dict(kwargs),
                }
            )
            or [],
        ),
    )
    simulation_context = SimulationStageContext(
        sim_result=SimpleNamespace(return_pct=-1.2),
        is_profit=False,
        trade_dicts=[{"code": "sh.600010"}],
        benchmark_passed=False,
        cycle_payload={"cycle_id": 12},
        research_artifacts={},
        research_feedback={"sample_count": 4},
        simulation_envelope=SimulationStageEnvelope(
            cycle_id=12,
            cutoff_date="20240201",
            regime="bear",
            execution_snapshot={},
        ),
    )

    monkeypatch.setattr(
        execution_services_module.logger,
        "warning",
        lambda *_args, **_kwargs: None,
    )

    service._apply_optimization_stage(
        controller,
        cycle_id=12,
        simulation_context=simulation_context,
        optimization_events=[],
    )

    assert "research_feedback_optimization" not in simulation_context.cycle_payload
    assert triggered
    optimization_input = triggered[0]["optimization_input"]
    assert optimization_input.research_feedback == {"sample_count": 4}
    assert optimization_input.research_feedback_optimization == {"triggered": False}
