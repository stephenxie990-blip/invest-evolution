import json
from pathlib import Path

from invest.leaderboard import build_leaderboard, collect_cycle_records, write_leaderboard


def _write_cycle(run_dir: Path, cycle_id: int, model_name: str, config_name: str, return_pct: float, sharpe: float, drawdown: float, regime: str, benchmark_passed: bool = True):
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cycle_id": cycle_id,
        "cutoff_date": "20250101",
        "return_pct": return_pct,
        "is_profit": return_pct > 0,
        "benchmark_passed": benchmark_passed,
        "model_name": model_name,
        "config_name": config_name,
        "self_assessment": {
            "regime": regime,
            "sharpe_ratio": sharpe,
            "max_drawdown": drawdown,
            "excess_return": return_pct / 2,
            "benchmark_passed": benchmark_passed,
        },
    }
    (run_dir / f"cycle_{cycle_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_leaderboard_groups_and_ranks_models(tmp_path):
    _write_cycle(tmp_path / "momentum_run", 1, "momentum", "momentum_v1", 5.0, 1.5, 6.0, "bull")
    _write_cycle(tmp_path / "momentum_run", 2, "momentum", "momentum_v1", 3.0, 1.2, 5.0, "bull")
    _write_cycle(tmp_path / "momentum_run", 3, "momentum", "momentum_v1", 2.0, 1.0, 4.0, "bull")
    _write_cycle(tmp_path / "defensive_run", 1, "defensive_low_vol", "defensive_low_vol_v1", 2.0, 1.8, 2.0, "bear")
    _write_cycle(tmp_path / "defensive_run", 2, "defensive_low_vol", "defensive_low_vol_v1", 1.2, 1.4, 2.4, "bear")
    _write_cycle(tmp_path / "defensive_run", 3, "defensive_low_vol", "defensive_low_vol_v1", 0.8, 1.3, 2.2, "bear")

    records = collect_cycle_records(tmp_path)
    leaderboard = build_leaderboard(records)

    assert leaderboard["total_records"] == 6
    assert leaderboard["total_models"] == 2
    assert leaderboard["entries"][0]["model_name"] in {"momentum", "defensive_low_vol"}
    assert any(entry["model_name"] == "defensive_low_vol" for entry in leaderboard["entries"])
    assert "bull" in leaderboard["regime_leaderboards"] or "bear" in leaderboard["regime_leaderboards"]


def test_write_leaderboard_outputs_json_file(tmp_path):
    _write_cycle(tmp_path / "value_run", 1, "value_quality", "value_quality_v1", 1.0, 0.9, 4.0, "oscillation")
    _write_cycle(tmp_path / "value_run", 2, "value_quality", "value_quality_v1", 0.8, 0.8, 3.5, "oscillation")
    _write_cycle(tmp_path / "value_run", 3, "value_quality", "value_quality_v1", 1.2, 1.0, 3.2, "oscillation")
    output = tmp_path / "leaderboard.json"
    data = write_leaderboard(tmp_path, output)
    assert output.exists()
    assert data["total_models"] == 1


def test_leaderboard_backfills_resolved_gate_policy_from_latest_config_ref(tmp_path):
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
                    "model_name": "momentum",
                    "config_name": "momentum_v2",
                    "self_assessment": {
                        "regime": "bull",
                        "sharpe_ratio": 1.1,
                        "max_drawdown": 3.0,
                        "excess_return": 0.4,
                        "overall_score": 0.72,
                    },
                    "run_context": {
                        "active_config_ref": str(config_path),
                    },
                    "lineage_record": {
                        "active_config_ref": str(config_path),
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
    from invest.leaderboard import write_leaderboard

    run_dir = tmp_path / 'momentum_case'
    run_dir.mkdir(parents=True, exist_ok=True)
    for cycle_id in (1, 2, 3):
        (run_dir / f'cycle_{cycle_id}.json').write_text(json.dumps({
            'cycle_id': cycle_id,
            'return_pct': 1.0,
            'is_profit': True,
            'benchmark_passed': True,
            'model_name': 'momentum',
            'config_name': 'momentum_v1',
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

    momentum = next(entry for entry in board['entries'] if entry['model_name'] == 'momentum')
    defensive = next(entry for entry in board['entries'] if entry['model_name'] == 'defensive_low_vol')

    assert momentum['eligible_for_routing'] is False
    assert momentum['ineligible_reason'] == 'min_cycles'
    assert defensive['eligible_for_routing'] is True
    assert board['best_model']['model_name'] == 'defensive_low_vol'
    assert all(item['model_name'] != 'momentum' for item in board['regime_leaderboards'].get('bull', []))


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
                    "model_name": "mean_reversion",
                    "config_name": "mean_reversion_v1",
                    "self_assessment": {
                        "regime": "oscillation",
                        "sharpe_ratio": 0.2,
                        "max_drawdown": 16.0,
                        "excess_return": -1.0,
                        "overall_score": 0.3,
                    },
                    "run_context": {
                        "active_config_ref": "configs/active.yaml",
                        "candidate_config_ref": "configs/candidate.yaml",
                        "promotion_decision": {"applied_to_active": False},
                        "deployment_stage": "candidate",
                    },
                    "lineage_record": {
                        "deployment_stage": "candidate",
                        "lineage_status": "candidate_pending",
                        "candidate_config_ref": "configs/candidate.yaml",
                        "active_config_ref": "configs/active.yaml",
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

    assert entry["eligible_for_routing"] is False
    assert entry["deployment_stage"] == "candidate"
    assert entry["ineligible_reason"].startswith("quality_gate:")
    assert any(item["name"] == "block_negative_score" and item["passed"] is False for item in entry["quality_gate"]["failed_checks"])
    assert any(item["name"] == "allowed_deployment_stages" and item["passed"] is False for item in entry["quality_gate"]["failed_checks"])


def test_leaderboard_quality_gate_blocks_regime_hard_fail(tmp_path):
    run_dir = tmp_path / "momentum_regime_fail"
    run_dir.mkdir(parents=True, exist_ok=True)
    cycles = [
        (1, -1.4, "bear", False),
        (2, -1.1, "bear", False),
        (3, 3.2, "bull", True),
    ]
    for cycle_id, return_pct, regime, benchmark_passed in cycles:
        (run_dir / f"cycle_{cycle_id}.json").write_text(
            json.dumps(
                {
                    "cycle_id": cycle_id,
                    "cutoff_date": "20250101",
                    "return_pct": return_pct,
                    "is_profit": return_pct > 0,
                    "benchmark_passed": benchmark_passed,
                    "model_name": "momentum",
                    "config_name": "momentum_v1",
                    "self_assessment": {
                        "regime": regime,
                        "sharpe_ratio": 1.0 if return_pct > 0 else 0.4,
                        "max_drawdown": 4.0 if return_pct > 0 else 6.5,
                        "excess_return": return_pct / 2,
                        "overall_score": 0.7 if return_pct > 0 else 0.5,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    board = write_leaderboard(tmp_path)
    entry = board["entries"][0]

    assert entry["eligible_for_routing"] is False
    assert entry["ineligible_reason"] == "quality_gate:regime_hard_fail.bear"
    assert entry["quality_gate"]["regime_hard_fail"]["failed_regime_names"] == ["bear"]


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
                    "model_name": "momentum",
                    "config_name": "momentum_v1",
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
