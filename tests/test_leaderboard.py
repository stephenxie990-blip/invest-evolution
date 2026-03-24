import json
from pathlib import Path

from invest_evolution.investment.governance import (
    build_leaderboard,
    build_leaderboard_payload,
    collect_cycle_records,
    write_leaderboard,
)


def _write_cycle(run_dir: Path, cycle_id: int, manager_id: str, manager_config_ref: str, return_pct: float, sharpe: float, drawdown: float, regime: str, benchmark_passed: bool = True):
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cycle_id": cycle_id,
        "cutoff_date": "20250101",
        "return_pct": return_pct,
        "is_profit": return_pct > 0,
        "benchmark_passed": benchmark_passed,
        "manager_id": manager_id,
        "manager_config_ref": manager_config_ref,
        "self_assessment": {
            "regime": regime,
            "sharpe_ratio": sharpe,
            "max_drawdown": drawdown,
            "excess_return": return_pct / 2,
            "benchmark_passed": benchmark_passed,
        },
    }
    (run_dir / f"cycle_{cycle_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_leaderboard_groups_and_ranks_managers(tmp_path):
    _write_cycle(tmp_path / "momentum_run", 1, "momentum", "momentum_v1", 5.0, 1.5, 6.0, "bull")
    _write_cycle(tmp_path / "momentum_run", 2, "momentum", "momentum_v1", 3.0, 1.2, 5.0, "bull")
    _write_cycle(tmp_path / "momentum_run", 3, "momentum", "momentum_v1", 2.0, 1.0, 4.0, "bull")
    _write_cycle(tmp_path / "defensive_run", 1, "defensive_low_vol", "defensive_low_vol_v1", 2.0, 1.8, 2.0, "bear")
    _write_cycle(tmp_path / "defensive_run", 2, "defensive_low_vol", "defensive_low_vol_v1", 1.2, 1.4, 2.4, "bear")
    _write_cycle(tmp_path / "defensive_run", 3, "defensive_low_vol", "defensive_low_vol_v1", 0.8, 1.3, 2.2, "bear")

    records = collect_cycle_records(tmp_path)
    leaderboard = build_leaderboard(records)

    assert leaderboard["total_records"] == 6
    assert leaderboard["total_managers"] == 2
    assert leaderboard["eligible_managers"] == 2
    assert leaderboard["entries"][0]["manager_id"] in {"momentum", "defensive_low_vol"}
    assert any(entry["manager_id"] == "defensive_low_vol" for entry in leaderboard["entries"])
    assert "bull" in leaderboard["regime_leaderboards"] or "bear" in leaderboard["regime_leaderboards"]


def test_write_leaderboard_outputs_json_file(tmp_path):
    _write_cycle(tmp_path / "value_run", 1, "value_quality", "value_quality_v1", 1.0, 0.9, 4.0, "oscillation")
    _write_cycle(tmp_path / "value_run", 2, "value_quality", "value_quality_v1", 0.8, 0.8, 3.5, "oscillation")
    _write_cycle(tmp_path / "value_run", 3, "value_quality", "value_quality_v1", 1.2, 1.0, 3.2, "oscillation")
    output = tmp_path / "leaderboard.json"
    data = write_leaderboard(tmp_path, output)
    assert output.exists()
    assert data["total_managers"] == 1
    assert data["eligible_managers"] == 1


def test_build_leaderboard_payload_does_not_create_json_file(tmp_path):
    _write_cycle(tmp_path / "value_run", 1, "value_quality", "value_quality_v1", 1.0, 0.9, 4.0, "oscillation")
    _write_cycle(tmp_path / "value_run", 2, "value_quality", "value_quality_v1", 0.8, 0.8, 3.5, "oscillation")
    _write_cycle(tmp_path / "value_run", 3, "value_quality", "value_quality_v1", 1.2, 1.0, 3.2, "oscillation")

    payload = build_leaderboard_payload(tmp_path)

    assert payload["total_managers"] == 1
    assert not (tmp_path / "leaderboard.json").exists()


def test_collect_cycle_records_excludes_trade_detail_files(tmp_path):
    run_dir = tmp_path / "strict_run"
    details_dir = run_dir / "details"
    _write_cycle(run_dir, 1, "momentum", "momentum_v1", 1.0, 0.9, 4.0, "bull")
    details_dir.mkdir(parents=True, exist_ok=True)
    (details_dir / "cycle_1_trades.json").write_text(
        json.dumps({"cycle_id": 1, "trades": [{"code": "sh.600519"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    records = collect_cycle_records(tmp_path)

    assert len(records) == 1
    assert records[0]["manager_id"] == "momentum"


def test_collect_cycle_records_excludes_proposal_store_artifacts(tmp_path):
    run_dir = tmp_path / "strict_run"
    proposal_store_dir = run_dir / "proposal_store"
    _write_cycle(run_dir, 1, "momentum", "momentum_v1", 1.0, 0.9, 4.0, "bull")
    proposal_store_dir.mkdir(parents=True, exist_ok=True)
    (proposal_store_dir / "cycle_0001_proposal_bundle_0001_deadbeef.json").write_text(
        json.dumps({"proposal_id": "proposal_1", "manager_id": "unknown"}, ensure_ascii=False),
        encoding="utf-8",
    )

    records = collect_cycle_records(tmp_path)

    assert len(records) == 1
    assert records[0]["manager_id"] == "momentum"


def test_leaderboard_backfills_resolved_gate_policy_from_latest_config_ref(tmp_path):
    import invest_evolution.investment.governance.engine as engine_module

    engine_module.PROJECT_ROOT = tmp_path
    config_path = tmp_path / "configs" / "momentum_v2.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "name: momentum_v2",
                "train:",
                "  promotion_gate:",
                "    min_samples: 4",
                "  freeze_gate:",
                "    governance:",
                "      max_candidate_pending_count: 0",
            ]
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "rerun"
    run_dir.mkdir(parents=True, exist_ok=True)
    for cycle_id in (1, 2, 3):
        (run_dir / f"cycle_{cycle_id}.json").write_text(
            json.dumps(
                {
                    "cycle_id": cycle_id,
                    "cutoff_date": "20250101",
                    "return_pct": 1.0,
                    "is_profit": True,
                    "benchmark_passed": True,
                    "manager_id": "momentum",
                    "manager_config_ref": "momentum_v2",
                    "self_assessment": {
                        "regime": "bull",
                        "sharpe_ratio": 1.1,
                        "max_drawdown": 3.0,
                        "excess_return": 0.4,
                        "overall_score": 0.72,
                    },
                    "run_context": {
                        "active_runtime_config_ref": str(config_path),
                    },
                    "lineage_record": {
                        "active_runtime_config_ref": str(config_path),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    board = write_leaderboard(tmp_path)

    assert board["policy"]["train"]["promotion_gate"]["min_samples"] == 4
    assert board["policy"]["train"]["freeze_gate"]["avg_sharpe_gte"] == 0.8
    assert board["policy"]["train"]["freeze_gate"]["governance"]["max_candidate_pending_count"] == 0
    assert board["policy"]["train"]["freeze_gate"]["governance"]["max_override_pending_count"] == 0


def test_leaderboard_carries_avg_strategy_score(tmp_path):
    from invest_evolution.investment.governance import write_leaderboard

    run_dir = tmp_path / 'momentum_case'
    run_dir.mkdir(parents=True, exist_ok=True)
    for cycle_id in (1, 2, 3):
        (run_dir / f'cycle_{cycle_id}.json').write_text(json.dumps({
            'cycle_id': cycle_id,
            'return_pct': 1.0,
            'is_profit': True,
            'benchmark_passed': True,
            'manager_id': 'momentum',
            'manager_config_ref': 'momentum_v1',
            'self_assessment': {'regime': 'bull', 'sharpe_ratio': 1.1, 'max_drawdown': 3.0, 'excess_return': 0.4, 'overall_score': 0.72},
        }, ensure_ascii=False), encoding='utf-8')
    board = write_leaderboard(tmp_path)
    assert board['entries'][0]['avg_strategy_score'] == 0.72


def test_leaderboard_marks_under_sampled_entries_ineligible(tmp_path):
    _write_cycle(tmp_path / 'momentum_run', 1, 'momentum', 'momentum_v1', 4.2, 1.3, 5.4, 'bull')
    _write_cycle(tmp_path / 'defensive_run', 1, 'defensive_low_vol', 'defensive_low_vol_v1', 0.8, 1.1, 2.5, 'bear')
    _write_cycle(tmp_path / 'defensive_run', 2, 'defensive_low_vol', 'defensive_low_vol_v1', 1.0, 1.2, 2.0, 'bear')
    _write_cycle(tmp_path / 'defensive_run', 3, 'defensive_low_vol', 'defensive_low_vol_v1', 0.6, 1.0, 1.8, 'bear')

    board = write_leaderboard(tmp_path)

    momentum = next(entry for entry in board['entries'] if entry['manager_id'] == 'momentum')
    defensive = next(entry for entry in board['entries'] if entry['manager_id'] == 'defensive_low_vol')

    assert momentum['eligible_for_governance'] is False
    assert momentum['ineligible_reason'] == 'min_cycles'
    assert defensive['eligible_for_governance'] is True
    assert board['best_entry']['manager_id'] == 'defensive_low_vol'
    assert all(item['manager_id'] != 'momentum' for item in board['regime_leaderboards'].get('bull', []))


def test_leaderboard_quality_gate_blocks_negative_score_and_candidate_stage(tmp_path):
    run_dir = tmp_path / "mean_reversion_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    for cycle_id, return_pct in ((1, -2.0), (2, -1.5), (3, -1.2)):
        (run_dir / f"cycle_{cycle_id}.json").write_text(
            json.dumps(
                {
                    "cycle_id": cycle_id,
                    "cutoff_date": "20250101",
                    "return_pct": return_pct,
                    "is_profit": False,
                    "benchmark_passed": False,
                    "manager_id": "mean_reversion",
                    "manager_config_ref": "mean_reversion_v1",
                    "self_assessment": {
                        "regime": "oscillation",
                        "sharpe_ratio": 0.2,
                        "max_drawdown": 16.0,
                        "excess_return": -1.0,
                        "overall_score": 0.3,
                    },
                    "run_context": {
                        "active_runtime_config_ref": "configs/active.yaml",
                        "candidate_runtime_config_ref": "configs/candidate.yaml",
                        "promotion_decision": {"applied_to_active": False},
                        "deployment_stage": "candidate",
                    },
                    "lineage_record": {
                        "deployment_stage": "candidate",
                        "lineage_status": "candidate_pending",
                        "candidate_runtime_config_ref": "configs/candidate.yaml",
                        "active_runtime_config_ref": "configs/active.yaml",
                    },
                    "promotion_record": {
                        "status": "candidate_pending",
                        "gate_status": "awaiting_gate",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    board = write_leaderboard(tmp_path)
    entry = board["entries"][0]

    assert entry["eligible_for_governance"] is False
    assert entry["deployment_stage"] == "candidate"
    assert entry["ineligible_reason"].startswith("quality_gate:")
    assert any(item["name"] == "block_negative_score" and item["passed"] is False for item in entry["quality_gate"]["failed_checks"])
    assert any(item["name"] == "allowed_deployment_stages" and item["passed"] is False for item in entry["quality_gate"]["failed_checks"])


def test_leaderboard_policy_uses_explicit_runtime_train_overrides(tmp_path):
    run_dir = tmp_path / "explicit_policy"
    run_dir.mkdir(parents=True, exist_ok=True)
    for cycle_id in (1, 2, 3):
        (run_dir / f"cycle_{cycle_id}.json").write_text(
            json.dumps(
                {
                    "cycle_id": cycle_id,
                    "cutoff_date": "20250101",
                    "return_pct": 0.4,
                    "is_profit": True,
                    "benchmark_passed": True,
                    "manager_id": "momentum",
                    "manager_config_ref": "momentum_v1",
                    "self_assessment": {
                        "regime": "bull",
                        "sharpe_ratio": 0.9,
                        "max_drawdown": 2.0,
                        "excess_return": 0.2,
                        "overall_score": 0.61,
                    },
                    "run_context": {
                        "resolved_train_policy": {
                            "promotion_gate": {"min_samples": 6},
                            "freeze_gate": {"benchmark_pass_rate_gte": 0.7},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    board = write_leaderboard(tmp_path)

    assert board["policy"]["train"]["promotion_gate"]["min_samples"] == 6
    assert board["policy"]["train"]["freeze_gate"]["benchmark_pass_rate_gte"] == 0.7


def test_leaderboard_exposes_regime_hard_fail_summary_from_quality_gate(tmp_path, monkeypatch):
    import invest_evolution.investment.governance.engine as engine_module

    _write_cycle(tmp_path / "momentum_run", 1, "momentum", "momentum_v1", 1.2, 1.1, 3.0, "bear")
    _write_cycle(tmp_path / "momentum_run", 2, "momentum", "momentum_v1", 1.0, 1.0, 2.8, "bear")
    _write_cycle(tmp_path / "momentum_run", 3, "momentum", "momentum_v1", 0.8, 0.9, 2.7, "bear")

    real_gate = engine_module.evaluate_governance_quality_gate

    def _patched_gate(entry, *, policy=None):
        payload = real_gate(entry, policy=policy)
        payload = dict(payload)
        failed_checks = list(payload.get("failed_checks") or [])
        failed_checks.append(
            {
                "name": "regime_hard_fail.bear",
                "passed": False,
                "actual": {"avg_return_pct": -0.6},
                "threshold": {"min_avg_return_pct": -0.5},
            }
        )
        checks = list(payload.get("checks") or [])
        checks.append(failed_checks[-1])
        payload["checks"] = checks
        payload["failed_checks"] = failed_checks
        payload["passed"] = False
        payload["regime_hard_fail"] = {
            "enabled": True,
            "passed": False,
            "failed_regime_names": ["bear"],
            "failed_regimes": [{"regime": "bear"}],
        }
        return payload

    monkeypatch.setattr(engine_module, "evaluate_governance_quality_gate", _patched_gate)

    board = write_leaderboard(tmp_path)
    entry = board["entries"][0]

    assert entry["eligible_for_governance"] is False
    assert entry["failed_regime_names"] == ["bear"]
    assert entry["regime_hard_fail"]["passed"] is False
    assert entry["regime_hard_fail"]["failed_regime_names"] == ["bear"]
    assert entry["ineligible_reason"] == "quality_gate:regime_hard_fail.bear"
