from types import SimpleNamespace

from invest_evolution.application.training.observability import (
    OptimizationBoundaryContext,
    build_runtime_mutation_boundary,
    build_review_boundary_event,
    emit_optimization_completed_boundary,
    emit_optimization_error_boundary,
    emit_optimization_start_boundary,
    finalize_review_boundary_effects,
    persist_research_boundary_effects,
    record_evolution_optimization_boundary_effects,
    record_feedback_optimization_boundary_effects,
    record_llm_optimization_boundary_effects,
    record_review_boundary_artifacts,
    record_runtime_mutation_boundary_effects,
    record_selection_boundary_effects,
    write_runtime_snapshot_boundary,
)


def test_record_selection_boundary_effects_writes_artifact_and_emits_events():
    calls = []
    controller = SimpleNamespace(
        artifact_recorder=SimpleNamespace(
            save_selection_artifact=lambda payload, cycle_id: calls.append(
                ("artifact", cycle_id, dict(payload))
            )
        ),
        agent_tracker=SimpleNamespace(
            mark_selected=lambda cycle_id, selected: calls.append(
                ("selected", cycle_id, list(selected))
            )
        ),
        _emit_agent_status=lambda *args, **kwargs: calls.append(("status", args, kwargs)),
        _emit_module_log=lambda *args, **kwargs: calls.append(("log", args, kwargs)),
    )

    record_selection_boundary_effects(
        controller,
        cycle_id=5,
        selected_codes=["sh.600519"],
        selection_trace={"dominant_manager_id": "momentum"},
        active_manager_count=2,
    )

    assert calls[0][0] == "artifact"
    assert calls[1][0] == "status"
    assert calls[2][0] == "log"
    assert calls[3] == ("selected", 5, ["sh.600519"])


def test_persist_research_boundary_effects_uses_projected_manager_identity():
    calls = []
    controller = SimpleNamespace(
        training_research_service=SimpleNamespace(
            persist_cycle_research_artifacts=lambda *args, **kwargs: {
                "saved_case_count": 1,
                "saved_attribution_count": 1,
            }
        ),
        _load_research_feedback=lambda **kwargs: calls.append(("feedback_request", dict(kwargs))) or {
            "recommendation": {"summary": "feedback-loaded"}
        },
        _research_feedback_brief=lambda payload: {"sample_count": int(payload is not None)},
        _emit_module_log=lambda *args, **kwargs: calls.append(("log", args, kwargs)),
    )

    research_artifacts, research_feedback = persist_research_boundary_effects(
        controller,
        cycle_id=7,
        cutoff_date="20240201",
        manager_output=SimpleNamespace(
            manager_id="value_quality",
            manager_config_ref="configs/value_quality.yaml",
        ),
        stock_data={"sh.600519": {"rows": 1}},
        selected=["sh.600519"],
        regime_result={"regime": "bear"},
        selection_mode="manager_portfolio",
        portfolio_plan={"active_manager_ids": ["value_quality"]},
        manager_results=[{"manager_id": "value_quality"}],
        execution_snapshot={},
        dominant_manager_id="value_quality",
    )

    assert research_artifacts["saved_case_count"] == 1
    assert research_feedback["recommendation"]["summary"] == "feedback-loaded"
    assert calls[0][1]["manager_id"] == "value_quality"
    assert calls[0][1]["manager_config_ref"].endswith("configs/value_quality.yaml")


def test_persist_research_boundary_effects_repairs_stale_snapshot_config_for_dominant_manager():
    calls = []
    controller = SimpleNamespace(
        training_research_service=SimpleNamespace(
            persist_cycle_research_artifacts=lambda *args, **kwargs: {
                "saved_case_count": 1,
                "saved_attribution_count": 1,
            }
        ),
        session_state=SimpleNamespace(
            default_manager_id="defensive_low_vol",
            default_manager_config_ref="configs/defensive_low_vol.yaml",
            last_governance_decision={
                "dominant_manager_id": "momentum",
                "active_manager_ids": ["momentum", "defensive_low_vol"],
                "manager_budget_weights": {"momentum": 0.65, "defensive_low_vol": 0.35},
                "regime": "bull",
            },
        ),
        _load_research_feedback=lambda **kwargs: calls.append(("feedback_request", dict(kwargs))) or {
            "recommendation": {"summary": "feedback-loaded"}
        },
        _research_feedback_brief=lambda payload: {"sample_count": int(payload is not None)},
        _emit_module_log=lambda *args, **kwargs: calls.append(("log", args, kwargs)),
    )

    persist_research_boundary_effects(
        controller,
        cycle_id=8,
        cutoff_date="20240201",
        manager_output=None,
        stock_data={"sh.600519": {"rows": 1}},
        selected=["sh.600519"],
        regime_result={"regime": "bull"},
        selection_mode="manager_portfolio",
        portfolio_plan={"active_manager_ids": ["momentum", "defensive_low_vol"]},
        manager_results=[],
        execution_snapshot={
            "dominant_manager_id": "momentum",
            "manager_config_ref": "defensive_low_vol_v1",
            "active_runtime_config_ref": "defensive_low_vol_v1",
            "execution_defaults": {
                "default_manager_id": "momentum",
                "default_manager_config_ref": "defensive_low_vol_v1",
            },
        },
        dominant_manager_id="momentum",
    )

    assert calls[0][1]["manager_id"] == "momentum"
    assert calls[0][1]["manager_config_ref"].endswith("momentum_v1.yaml")


def test_write_runtime_snapshot_boundary_delegates_to_config_service(tmp_path):
    controller = SimpleNamespace(
        output_dir=tmp_path / "training",
        config_service=SimpleNamespace(
            write_runtime_snapshot=lambda **kwargs: tmp_path / "snapshots" / f"cycle_{kwargs['cycle_id']}.yaml"
        ),
    )

    path = write_runtime_snapshot_boundary(controller, cycle_id=11)

    assert path.endswith("cycle_11.yaml")


def test_review_boundary_effects_record_artifacts_and_finalize_event():
    saved = []
    appended = []
    logs = []

    class DummyEvent:
        def __init__(self, **kwargs):
            self.cycle_id = kwargs.get("cycle_id")
            self.trigger = kwargs.get("trigger")
            self.stage = kwargs.get("stage")
            self.status = kwargs.get("status", "ok")
            self.suggestions = list(kwargs.get("suggestions", []))
            self.decision = dict(kwargs.get("decision", {}))
            self.applied_change = dict(kwargs.get("applied_change", {}))
            self.lineage = dict(kwargs.get("lineage", {}))
            self.evidence = dict(kwargs.get("evidence", {}))
            self.notes = str(kwargs.get("notes", ""))
            self.review_applied_effects_payload = dict(
                kwargs.get("review_applied_effects_payload", {})
            )
            self.review_decision_payload = dict(kwargs.get("review_decision_payload", {}))
            self.research_feedback_payload = dict(kwargs.get("research_feedback_payload", {}))
            self.llm_analysis_payload = dict(kwargs.get("llm_analysis_payload", {}))
            self.evolution_engine_payload = dict(kwargs.get("evolution_engine_payload", {}))
            self.runtime_config_mutation_payload = dict(
                kwargs.get("runtime_config_mutation_payload", {})
            )
            self.runtime_config_mutation_skipped_payload = dict(
                kwargs.get("runtime_config_mutation_skipped_payload", {})
            )
            self.optimization_error_payload = dict(kwargs.get("optimization_error_payload", {}))

        def to_dict(self):
            payload = {
                "cycle_id": self.cycle_id,
                "trigger": self.trigger,
                "stage": self.stage,
                "status": self.status,
                "suggestions": list(self.suggestions),
                "decision": dict(self.decision),
                "applied_change": dict(self.applied_change),
                "lineage": dict(self.lineage),
                "evidence": dict(self.evidence),
                "notes": self.notes,
            }
            if self.review_decision_payload:
                payload["review_decision_payload"] = dict(self.review_decision_payload)
            if self.review_applied_effects_payload:
                payload["review_applied_effects_payload"] = dict(
                    self.review_applied_effects_payload
                )
            if self.research_feedback_payload:
                payload["research_feedback_payload"] = dict(self.research_feedback_payload)
            if self.llm_analysis_payload:
                payload["llm_analysis_payload"] = dict(self.llm_analysis_payload)
            if self.evolution_engine_payload:
                payload["evolution_engine_payload"] = dict(self.evolution_engine_payload)
            if self.runtime_config_mutation_payload:
                payload["runtime_config_mutation_payload"] = dict(
                    self.runtime_config_mutation_payload
                )
            if self.runtime_config_mutation_skipped_payload:
                payload["runtime_config_mutation_skipped_payload"] = dict(
                    self.runtime_config_mutation_skipped_payload
                )
            if self.optimization_error_payload:
                payload["optimization_error_payload"] = dict(self.optimization_error_payload)
            return payload

    controller = SimpleNamespace(
        default_manager_id="value_quality",
        default_manager_config_ref="configs/value_quality.yaml",
        experiment_review_window={"mode": "rolling", "size": 2},
        cycle_history=[SimpleNamespace(cycle_id=4), SimpleNamespace(cycle_id=5)],
        artifact_recorder=SimpleNamespace(
            save_manager_review_artifact=lambda report, cycle_id: saved.append(
                ("manager", cycle_id, dict(report))
            ),
            save_allocation_review_artifact=lambda report, cycle_id: saved.append(
                ("allocation", cycle_id, dict(report))
            ),
        ),
        _append_optimization_event=lambda event: appended.append(event.to_dict()),
        _emit_module_log=lambda *args, **kwargs: logs.append((args, kwargs)),
    )

    manager_review_report = {
        "summary": {
            "manager_count": 1,
            "active_manager_ids": ["value_quality"],
            "verdict_counts": {},
        }
    }
    allocation_review_report = {"verdict": "rebalance"}
    review_decision = {
        "reasoning": "rebalance after dual review",
        "strategy_suggestions": ["rebalance_portfolio_constraints"],
        "param_adjustments": {"position_size": 0.1},
        "agent_weight_adjustments": {},
        "manager_budget_adjustments": {"value_quality": 0.8},
    }
    review_trace = {"decision_source": "dual_review"}

    record_review_boundary_artifacts(
        controller,
        cycle_id=8,
        manager_review_report=manager_review_report,
        allocation_review_report=allocation_review_report,
    )
    bundle = build_review_boundary_event(
        controller,
        cycle_id=8,
        manager_output=SimpleNamespace(
            manager_id="value_quality",
            manager_config_ref="configs/value_quality.yaml",
        ),
        execution_snapshot={"manager_config_ref": "configs/value_quality.yaml"},
        dominant_manager_id="value_quality",
        optimization_event_factory=DummyEvent,
        review_decision=review_decision,
        eval_report=SimpleNamespace(return_pct=1.2, benchmark_passed=True),
        manager_review_report=manager_review_report,
        allocation_review_report=allocation_review_report,
    )
    bundle.review_event.review_applied_effects_payload = {
        "param_adjustments": {"position_size": 0.1},
        "manager_budget_adjustments": {"value_quality": 0.8},
    }
    finalize_review_boundary_effects(
        controller,
        cycle_id=8,
        review_decision=review_decision,
        review_trace=review_trace,
        manager_review_report=manager_review_report,
        allocation_review_report=allocation_review_report,
        review_event=bundle.review_event,
        review_applied=True,
        review_basis_window=bundle.review_basis_window,
        manager_id=bundle.manager_id,
        active_runtime_config_ref=bundle.active_runtime_config_ref,
    )

    assert saved[0][0] == "manager"
    assert saved[1][0] == "allocation"
    assert bundle.review_event.stage == "review_decision"
    assert bundle.review_basis_window["cycle_ids"] == [5, 8]
    assert appended[0]["lineage"]["manager_id"] == "value_quality"
    assert appended[0]["lineage"]["promotion_status"] == "override_pending"
    assert appended[0]["lineage"]["runtime_override_keys"] == [
        "position_size",
        "value_quality",
    ]
    assert logs[0][1]["kind"] == "review_decision"


def test_optimization_boundary_effects_emit_start_and_feedback_log():
    statuses = []
    logs = []
    appended = []
    controller = SimpleNamespace(
        _emit_agent_status=lambda *args, **kwargs: statuses.append((args, kwargs)),
        _emit_module_log=lambda *args, **kwargs: logs.append((args, kwargs)),
        _append_optimization_event=lambda event: appended.append(event.to_dict()),
    )

    class DummyEvent:
        research_feedback_payload = {"param_adjustments": {"position_size": 0.08}}

        @staticmethod
        def to_dict():
            return {
                "stage": "research_feedback",
                "research_feedback_payload": {"param_adjustments": {"position_size": 0.08}},
            }

    emit_optimization_start_boundary(
        controller,
        cycle_id=12,
        opening_message="ask 侧校准反馈触发自我优化...",
        opening_details={"sample_count": 6},
    )
    record_feedback_optimization_boundary_effects(
        controller,
        cycle_id=12,
        feedback_plan={
            "summary": "tighten after calibration drift",
            "failed_check_names": ["T+20.hit_rate"],
            "sample_count": 6,
        },
        feedback_event=DummyEvent(),
    )

    assert statuses[0][0][0] == "EvolutionOptimizer"
    assert statuses[0][1]["stage"] == "optimization"
    assert logs[0][1]["kind"] == "optimization_start"
    assert appended[0]["stage"] == "research_feedback"
    assert logs[1][1]["kind"] == "research_feedback_gate"


def test_heavy_optimization_boundary_effects_cover_llm_evolution_mutation_and_status():
    statuses = []
    logs = []
    speeches = []
    appended = []
    reloaded = []

    controller = SimpleNamespace(
        _emit_agent_status=lambda *args, **kwargs: statuses.append((args, kwargs)),
        _emit_module_log=lambda *args, **kwargs: logs.append((args, kwargs)),
        _emit_meeting_speech=lambda *args, **kwargs: speeches.append((args, kwargs)),
        _append_optimization_event=lambda event: appended.append(event.to_dict()),
        _reload_manager_runtime=lambda path: reloaded.append(path),
    )

    class DummyEvent:
        def __init__(self, stage: str, evidence=None):
            self.stage = stage
            self.evidence = dict(evidence or {})

        def to_dict(self):
            return {"stage": self.stage, "evidence": dict(self.evidence)}

    analysis = SimpleNamespace(cause="drawdown from chasing breakouts", suggestions=["reduce size"])

    record_llm_optimization_boundary_effects(
        controller,
        cycle_id=15,
        llm_event=DummyEvent("llm_analysis"),
        analysis=analysis,
        adjustments={"position_size": 0.1},
    )
    record_evolution_optimization_boundary_effects(
        controller,
        cycle_id=15,
        evo_event=DummyEvent("evolution_engine"),
        best_params={"take_profit_pct": 0.2},
        fitness_scores=[1.0, 2.0, 3.0],
    )
    record_runtime_mutation_boundary_effects(
        controller,
        cycle_id=15,
        mutation_event=DummyEvent(
            "runtime_config_mutation",
            evidence={"auto_applied": True},
        ),
        mutation_log_message="candidate promoted to active",
        adjustment_count=2,
        auto_apply_runtime_config_ref="configs/generated.yaml",
    )
    emit_optimization_error_boundary(
        controller,
        cycle_id=15,
        err_event=DummyEvent("optimization_error"),
        exc=RuntimeError("boom"),
    )
    emit_optimization_completed_boundary(
        controller,
        cycle_id=15,
        event_count=4,
        trigger_reason="consecutive_losses",
    )

    assert appended[0]["stage"] == "llm_analysis"
    assert appended[1]["stage"] == "evolution_engine"
    assert appended[2]["stage"] == "runtime_config_mutation"
    assert appended[3]["stage"] == "optimization_error"
    assert speeches[0][1]["role"] == "optimizer"
    assert logs[0][1]["kind"] == "llm_analysis"
    assert logs[1][1]["kind"] == "evolution_engine"
    assert logs[2][1]["kind"] == "runtime_config_mutation"
    assert reloaded == ["configs/generated.yaml"]
    assert statuses[0][0][1] == "error"
    assert statuses[1][0][1] == "completed"


def test_build_runtime_mutation_boundary_handles_pending_and_generated_candidates():
    class DummyEvent:
        def __init__(self, **kwargs):
            self.stage = kwargs["stage"]
            self.decision = dict(kwargs.get("decision", {}))
            self.evidence = dict(kwargs.get("evidence", {}))
            self.runtime_config_mutation_payload = dict(
                kwargs.get("runtime_config_mutation_payload", {})
            )
            self.runtime_config_mutation_skipped_payload = dict(
                kwargs.get("runtime_config_mutation_skipped_payload", {})
            )

        def to_dict(self):
            payload = {
                "stage": self.stage,
                "decision": dict(self.decision),
                "evidence": dict(self.evidence),
            }
            if self.runtime_config_mutation_payload:
                payload["runtime_config_mutation_payload"] = dict(
                    self.runtime_config_mutation_payload
                )
            if self.runtime_config_mutation_skipped_payload:
                payload["runtime_config_mutation_skipped_payload"] = dict(
                    self.runtime_config_mutation_skipped_payload
                )
            return payload

    pending_controller = SimpleNamespace(
        auto_apply_mutation=False,
        cycle_history=[
            SimpleNamespace(
                lineage_record={
                    "deployment_stage": "candidate",
                    "lineage_status": "candidate_pending",
                    "candidate_runtime_config_ref": "/tmp/pending.yaml",
                }
            )
        ],
    )
    pending_bundle = build_runtime_mutation_boundary(
        pending_controller,
        context=OptimizationBoundaryContext(
            cycle_id=21,
            manager_id="momentum",
            active_runtime_config_ref="configs/active.yaml",
            fitness_source_cycles=[18, 19, 20],
        ),
        cycle_id=21,
        trigger_reason="consecutive_losses",
        active_runtime_config_ref="configs/active.yaml",
        config_adjustments={"position_size": 0.1},
        scoring_adjustments={},
        feedback_plan=None,
        event_factory=DummyEvent,
    )

    assert pending_bundle.mutation_event.stage == "runtime_config_mutation_skipped"
    assert pending_bundle.auto_apply_runtime_config_ref == ""
    assert pending_bundle.mutation_event.runtime_config_mutation_skipped_payload == {
        "skipped": True,
        "pending_candidate_runtime_config_ref": "/tmp/pending.yaml",
        "auto_applied": False,
        "param_adjustments": {"position_size": 0.1},
        "scoring_adjustments": {},
        "skip_reason": "pending_candidate_unresolved",
    }

    calls = []
    generated_controller = SimpleNamespace(
        auto_apply_mutation=True,
        cycle_history=[],
        runtime_config_mutator=SimpleNamespace(
            mutate=lambda runtime_config_ref, **kwargs: calls.append((runtime_config_ref, dict(kwargs))) or {
                "runtime_config_ref": "configs/generated.yaml",
                "meta": {"generation_label": kwargs["generation_label"]},
            }
        ),
    )
    generated_bundle = build_runtime_mutation_boundary(
        generated_controller,
        context=OptimizationBoundaryContext(
            cycle_id=22,
            manager_id="value_quality",
            active_runtime_config_ref="configs/active.yaml",
            fitness_source_cycles=[20, 21],
        ),
        cycle_id=22,
        trigger_reason="research_feedback",
        active_runtime_config_ref="configs/active.yaml",
        config_adjustments={"position_size": 0.1},
        scoring_adjustments={"weights": {"alpha": 1.2}},
        feedback_plan={"recommendation": {"bias": "tighten_risk"}},
        event_factory=DummyEvent,
    )

    assert calls[0][0] == "configs/active.yaml"
    assert generated_bundle.mutation_event.stage == "runtime_config_mutation"
    assert generated_bundle.auto_apply_runtime_config_ref == "configs/generated.yaml"
    assert generated_bundle.mutation_event.runtime_config_mutation_payload == {
        "runtime_config_ref": "configs/generated.yaml",
        "auto_applied": True,
        "param_adjustments": {"position_size": 0.1},
        "scoring_adjustments": {"weights": {"alpha": 1.2}},
        "mutation_meta": {"generation_label": "cycle_0022"},
    }
