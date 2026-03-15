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
