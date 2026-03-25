from types import SimpleNamespace

from invest_evolution.application.training import execution as execution_module


def test_resolve_training_callable_and_optional_call_helpers():
    resolved = execution_module._resolve_training_callable(
        execution_module.training_observability,
        "build_optimization_lineage",
    )
    assert callable(resolved)

    assert (
        execution_module._resolve_training_callable(
            execution_module.training_observability,
            "_definitely_missing_callable",
        )
        is None
    )
    assert (
        execution_module._call_training_if_available(
            execution_module.training_observability,
            "_definitely_missing_callable",
            1,
            named="x",
        )
        is None
    )


def test_resolve_candidate_proposal_gate_prefers_training_policy(monkeypatch):
    observed = {"called": False}

    def _policy_gate(*_args, **_kwargs):
        observed["called"] = True
        return {
            "passed": True,
            "filtered_adjustments": {
                "params": {"position_size": 0.11},
                "scoring": {},
                "agent_weights": {},
                "proposal_refs": ["proposal_0001_001"],
            },
            "blocked_adjustments": {"params": {}, "scoring": {}, "agent_weights": {}},
            "blocked_proposals": [],
            "proposal_summary": {
                "requested_source_summary": {"review.param_adjustment": 1},
                "approved_proposal_refs": ["proposal_0001_001"],
            },
        }

    def _shared_gate_should_not_run(*_args, **_kwargs):
        raise AssertionError("shared policy gate should not run when training policy gate returns first")

    monkeypatch.setattr(
        execution_module.training_policy,
        "evaluate_candidate_proposal_gate",
        _policy_gate,
        raising=False,
    )
    monkeypatch.setattr(
        execution_module.shared_policy_module,
        "evaluate_candidate_proposal_gate",
        _shared_gate_should_not_run,
        raising=False,
    )

    result = execution_module._resolve_candidate_proposal_gate(
        SimpleNamespace(),
        cycle_id=1,
        proposal_bundle={
            "proposals": [
                {
                    "proposal_id": "proposal_0001_001",
                    "source": "review.param_adjustment",
                    "target_scope": "candidate",
                    "patch": {"position_size": 0.11},
                }
            ]
        },
    )

    assert observed["called"] is True
    assert result["filtered_adjustments"]["params"] == {"position_size": 0.11}
    assert result["proposal_summary"]["approved_proposal_refs"] == ["proposal_0001_001"]
