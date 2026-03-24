from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from invest_evolution.investment.shared.policy import (
    evaluate_candidate_proposal_gate,
    evaluate_governance_quality_gate,
    evaluate_regime_hard_fail,
    normalize_proposal_gate_policy,
    normalize_strategy_family_name,
    resolve_governance_matrix,
    resolve_strategy_family_regime_hard_fail_profile,
)


class _ControllerStub:
    def __init__(self, proposal_gate_policy: dict[str, Any] | None = None):
        self.proposal_gate_policy = dict(proposal_gate_policy or {})
        self.current_params = {
            "position_size": 0.10,
            "stop_loss_pct": 0.05,
        }


def test_strategy_family_normalization_and_profile_resolution():
    assert normalize_strategy_family_name("Momentum_v2") == "momentum"
    assert normalize_strategy_family_name("value_quality-pro") == "value_quality"
    assert normalize_strategy_family_name("") == ""

    profile = resolve_strategy_family_regime_hard_fail_profile("momentum_v2")
    assert profile["critical_regimes"] == ["bull", "bear"]
    assert "bull" in profile["per_regime"]


def test_resolve_governance_matrix_applies_strategy_family_and_shared_overrides():
    matrix = resolve_governance_matrix(
        {
            "shared_regime_hard_fail": {
                "critical_regimes": ["bear"],
                "min_cycles": 4,
            }
        },
        strategy_family="momentum_v2",
    )

    governance_policy = matrix["governance"]["regime_hard_fail"]
    promotion_policy = matrix["promotion"]["regime_hard_fail"]
    assert governance_policy["critical_regimes"] == ["bear"]
    assert governance_policy["min_cycles"] == 4
    assert promotion_policy["critical_regimes"] == ["bear"]
    assert promotion_policy["min_cycles"] == 4


def test_evaluate_regime_hard_fail_marks_failed_regime():
    result = evaluate_regime_hard_fail(
        {
            "bear": {
                "cycles": 3,
                "avg_return_pct": -0.8,
                "benchmark_pass_rate": 0.0,
                "win_rate": 0.0,
                "loss_cycles": 3,
                "negative_contribution_pct": -2.4,
            }
        },
        policy={
            "enabled": True,
            "critical_regimes": ["bear"],
            "min_cycles": 2,
            "min_avg_return_pct": -0.2,
            "max_benchmark_pass_rate": 0.1,
            "max_win_rate": 0.2,
        },
    )

    assert result["passed"] is False
    assert result["failed_regime_names"] == ["bear"]
    assert result["failed_checks"][0]["name"] == "regime_hard_fail.bear"


def test_evaluate_governance_quality_gate_includes_regime_hard_fail_checks():
    gate = evaluate_governance_quality_gate(
        {
            "score": 0.6,
            "avg_return_pct": 0.2,
            "avg_strategy_score": 0.7,
            "benchmark_pass_rate": 0.8,
            "avg_max_drawdown": 5.0,
            "deployment_stage": "active",
            "strategy_family": "momentum_v2",
            "regime_performance": {
                "bear": {
                    "cycles": 3,
                    "avg_return_pct": -0.9,
                    "benchmark_pass_rate": 0.0,
                    "win_rate": 0.0,
                    "loss_cycles": 3,
                    "negative_contribution_pct": -2.7,
                }
            },
        },
        policy={
            "regime_hard_fail": {
                "enabled": True,
                "critical_regimes": ["bear"],
                "min_cycles": 2,
                "min_avg_return_pct": -0.2,
                "max_benchmark_pass_rate": 0.1,
                "max_win_rate": 0.2,
            }
        },
    )

    assert gate["passed"] is False
    assert gate["regime_hard_fail"]["failed_regime_names"] == ["bear"]
    assert any(
        check["name"] == "regime_hard_fail.bear" and check["passed"] is False
        for check in gate["checks"]
    )


def test_normalize_proposal_gate_policy_merges_defaults():
    policy = normalize_proposal_gate_policy(
        {
            "identity_protection": {
                "max_single_step_ratio_vs_baseline": 0.2,
            },
            "profitable_cycle": {
                "block_scoring_adjustments": False,
            },
        }
    )
    assert policy["identity_protection"]["max_single_step_ratio_vs_baseline"] == 0.2
    assert policy["profitable_cycle"]["block_scoring_adjustments"] is False
    assert "protected_params" in policy


def test_evaluate_candidate_proposal_gate_filters_drift_and_profit_frozen_scoring(
    tmp_path: Path,
):
    active_config = tmp_path / "active.yaml"
    active_config.write_text(
        yaml.safe_dump(
            {
                "kind": "momentum",
                "params": {
                    "position_size": 0.10,
                    "stop_loss_pct": 0.05,
                },
                "scoring": {
                    "alpha": 0.50,
                },
                "agent_weights": {
                    "momentum": 1.0,
                },
            },
            allow_unicode=False,
        ),
        encoding="utf-8",
    )

    controller = _ControllerStub()
    result = evaluate_candidate_proposal_gate(
        controller,
        cycle_id=12,
        proposal_bundle={
            "active_runtime_config_ref": str(active_config),
            "execution_snapshot": {
                "is_profit": True,
                "return_pct": 1.2,
                "benchmark_passed": True,
                "runtime_overrides": {
                    "position_size": 0.10,
                    "stop_loss_pct": 0.05,
                },
            },
            "proposals": [
                {
                    "proposal_id": "proposal_param_blocked",
                    "source": "review",
                    "target_scope": "candidate",
                    "metadata": {"proposal_kind": "runtime_param_adjustment"},
                    "patch": {"position_size": 0.90},
                },
                {
                    "proposal_id": "proposal_param_allowed",
                    "source": "review",
                    "target_scope": "candidate",
                    "metadata": {"proposal_kind": "runtime_param_adjustment"},
                    "patch": {"stop_loss_pct": 0.04},
                },
                {
                    "proposal_id": "proposal_scoring_blocked",
                    "source": "review",
                    "target_scope": "candidate",
                    "metadata": {"proposal_kind": "scoring_adjustment"},
                    "patch": {"alpha": 0.60},
                },
            ],
        },
    )

    assert result["approved"] is True
    assert result["filtered_adjustments"]["params"] == {"stop_loss_pct": 0.04}
    assert result["blocked_adjustments"]["params"] == {"position_size": 0.90}
    assert result["blocked_adjustments"]["scoring"] == {"alpha": 0.60}
    assert result["proposal_summary"]["blocked_proposal_count"] >= 2
    assert "profitable_cycle_behavior_frozen" in result["proposal_summary"]["block_reason_counts"]
    assert "profitable_cycle_scoring_frozen" in result["proposal_summary"]["block_reason_counts"]


def test_evaluate_candidate_proposal_gate_disabled_policy_passthrough(tmp_path: Path):
    active_config = tmp_path / "active.yaml"
    active_config.write_text(
        yaml.safe_dump({"params": {"position_size": 0.10}}, allow_unicode=False),
        encoding="utf-8",
    )
    controller = _ControllerStub({"enabled": False})
    result = evaluate_candidate_proposal_gate(
        controller,
        cycle_id=1,
        proposal_bundle={
            "active_runtime_config_ref": str(active_config),
            "proposals": [
                {
                    "proposal_id": "proposal_1",
                    "source": "research",
                    "target_scope": "candidate",
                    "metadata": {"proposal_kind": "runtime_param_adjustment"},
                    "patch": {"position_size": 0.12},
                }
            ],
        },
    )

    assert result["approved"] is True
    assert result["blocked_proposals"] == []
    assert result["filtered_adjustments"]["params"] == {"position_size": 0.12}


def test_evaluate_candidate_proposal_gate_non_profit_enforces_identity_drift(
    tmp_path: Path,
):
    active_config = tmp_path / "active.yaml"
    active_config.write_text(
        yaml.safe_dump({"params": {"position_size": 0.10}}, allow_unicode=False),
        encoding="utf-8",
    )
    controller = _ControllerStub()
    result = evaluate_candidate_proposal_gate(
        controller,
        cycle_id=2,
        proposal_bundle={
            "active_runtime_config_ref": str(active_config),
            "execution_snapshot": {
                "is_profit": False,
                "return_pct": -0.4,
                "runtime_overrides": {
                    "position_size": 0.10,
                },
            },
            "proposals": [
                {
                    "proposal_id": "proposal_drift_blocked",
                    "source": "review",
                    "target_scope": "candidate",
                    "metadata": {"proposal_kind": "runtime_param_adjustment"},
                    "patch": {"position_size": 0.90},
                }
            ],
        },
    )
    assert result["approved"] is False
    assert result["blocked_adjustments"]["params"] == {"position_size": 0.90}
    assert (
        result["proposal_summary"]["block_reason_counts"][
            "single_step_identity_drift_exceeded"
        ]
        >= 1
    )
