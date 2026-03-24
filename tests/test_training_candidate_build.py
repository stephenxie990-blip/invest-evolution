from types import SimpleNamespace

from invest_evolution.application.training import execution as execution_module
from invest_evolution.application.training.controller import TrainingSessionState


class _Event:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def to_dict(self):
        return dict(self.__dict__)


class _MutatorStub:
    def __init__(self, result=None):
        self.calls: list[tuple[str, dict]] = []
        self.result = dict(
            result
            or {
                "runtime_config_ref": "src/invest_evolution/investment/runtimes/configs/generated_candidate.yaml",
                "meta_path": "outputs/training/runtime_generations/generated_candidate.json",
                "meta": {"version_id": "candidate_v1"},
            }
        )

    def mutate(self, runtime_config_ref, **kwargs):
        self.calls.append((str(runtime_config_ref), dict(kwargs)))
        return dict(self.result)


def _candidate_proposal(proposal_id: str = "proposal_0001_001") -> dict:
    return {
        "proposal_id": proposal_id,
        "suggestion_id": proposal_id.replace("proposal", "suggestion"),
        "cycle_id": 1,
        "source": "review.param_adjustment",
        "target_scope": "candidate",
        "patch": {"position_size": 0.12},
        "metadata": {"proposal_kind": "param_adjustment"},
    }


def _build_controller(*, tmp_path, cycle_history=None, auto_apply=False, mutator=None):
    appended: list[object] = []
    logs: list[tuple[tuple, dict]] = []
    controller = SimpleNamespace(
        session_state=TrainingSessionState(
            current_params={"position_size": 0.2},
            cycle_history=list(cycle_history or []),
            default_manager_id="momentum",
            default_manager_config_ref="src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml",
        ),
        output_dir=str(tmp_path),
        model_name="momentum",
        model_config_path="src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml",
        auto_apply_mutation=bool(auto_apply),
        runtime_config_mutator=mutator or _MutatorStub(),
        governance_enabled=False,
        governance_mode="off",
        last_governance_decision={},
        risk_policy={},
        review_policy={},
    )
    controller._append_optimization_event = lambda event: appended.append(event)
    controller._emit_module_log = lambda *args, **kwargs: logs.append((args, kwargs))
    return controller, appended, logs


def test_build_cycle_candidate_from_proposals_returns_none_when_no_proposals(tmp_path):
    controller, appended, logs = _build_controller(tmp_path=tmp_path)
    controller.current_cycle_learning_proposals = []

    event = execution_module.build_cycle_candidate_from_proposals(
        controller,
        cycle_id=1,
        event_factory=_Event,
    )

    assert event is None
    assert appended == []
    assert logs == []


def test_build_cycle_candidate_from_proposals_skips_when_gate_rejects_all(
    tmp_path,
    monkeypatch,
):
    controller, appended, _logs = _build_controller(tmp_path=tmp_path)
    proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0001_deadbeef",
        "bundle_path": "",
        "active_runtime_config_ref": "src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml",
        "proposals": [_candidate_proposal()],
    }

    monkeypatch.setattr(
        execution_module,
        "_resolve_candidate_proposal_gate",
        lambda *_args, **_kwargs: {
            "filtered_adjustments": {"params": {}, "scoring": {}, "agent_weights": {}, "proposal_refs": []},
            "blocked_adjustments": {"params": {"position_size": "blocked"}, "scoring": {}, "agent_weights": {}},
            "blocked_proposals": [
                {"proposal_id": "proposal_0001_001", "block_reasons": ["identity_drift_exceeded"]}
            ],
            "proposal_summary": {
                "requested_source_summary": {"review.param_adjustment": 1},
                "approved_proposal_refs": [],
            },
        },
    )

    event = execution_module.build_cycle_candidate_from_proposals(
        controller,
        cycle_id=1,
        proposal_bundle=proposal_bundle,
        event_factory=_Event,
    )

    assert event is not None
    assert event.stage == "candidate_build_skipped"
    assert event.decision["skip_reason"] == "proposal_governance_rejected"
    assert event.applied_change["proposal_count"] == 0
    assert appended == []


def test_build_cycle_candidate_from_proposals_skips_when_pending_candidate_exists(tmp_path):
    pending_candidate_ref = "src/invest_evolution/investment/runtimes/configs/pending_candidate.yaml"
    cycle_history = [
        {
            "cycle_id": 7,
            "lineage_record": {
                "deployment_stage": "candidate",
                "lineage_status": "candidate_pending",
                "candidate_runtime_config_ref": pending_candidate_ref,
            },
        }
    ]
    mutator = _MutatorStub()
    controller, appended, _logs = _build_controller(
        tmp_path=tmp_path,
        cycle_history=cycle_history,
        mutator=mutator,
    )
    proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0008_abcdef12",
        "bundle_path": "",
        "active_runtime_config_ref": "src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml",
        "proposals": [_candidate_proposal()],
    }

    event = execution_module.build_cycle_candidate_from_proposals(
        controller,
        cycle_id=8,
        proposal_bundle=proposal_bundle,
        event_factory=_Event,
    )

    assert event is not None
    assert event.stage == "candidate_build_skipped"
    assert event.decision["skip_reason"] == "pending_candidate_unresolved"
    assert event.decision["pending_candidate_ref"]
    assert mutator.calls == []
    assert appended == []


def test_build_cycle_candidate_from_proposals_builds_candidate_event(tmp_path):
    mutator = _MutatorStub()
    controller, appended, logs = _build_controller(tmp_path=tmp_path, mutator=mutator)
    proposal_bundle = {
        "proposal_bundle_id": "proposal_bundle_0003_12345678",
        "bundle_path": "",
        "active_runtime_config_ref": "src/invest_evolution/investment/runtimes/configs/momentum_v1.yaml",
        "proposals": [_candidate_proposal("proposal_0003_001")],
    }

    event = execution_module.build_cycle_candidate_from_proposals(
        controller,
        cycle_id=3,
        proposal_bundle=proposal_bundle,
        event_factory=_Event,
    )

    assert event is not None
    assert event.stage == "candidate_build"
    assert event.decision["runtime_config_ref"]
    assert event.applied_change["proposal_count"] == 1
    assert event.lineage["deployment_stage"] == "candidate"
    assert len(mutator.calls) == 1
    assert len(appended) == 1
    assert logs
    assert logs[0][1]["kind"] == "candidate_build"
