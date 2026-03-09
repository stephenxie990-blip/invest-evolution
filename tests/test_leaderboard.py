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
    _write_cycle(tmp_path / "defensive_run", 1, "defensive_low_vol", "defensive_low_vol_v1", 2.0, 1.8, 2.0, "bear")

    records = collect_cycle_records(tmp_path)
    leaderboard = build_leaderboard(records)

    assert leaderboard["total_records"] == 3
    assert leaderboard["total_models"] == 2
    assert leaderboard["entries"][0]["model_name"] in {"momentum", "defensive_low_vol"}
    assert any(entry["model_name"] == "defensive_low_vol" for entry in leaderboard["entries"])
    assert "bull" in leaderboard["regime_leaderboards"] or "bear" in leaderboard["regime_leaderboards"]


def test_write_leaderboard_outputs_json_file(tmp_path):
    _write_cycle(tmp_path / "value_run", 1, "value_quality", "value_quality_v1", 1.0, 0.9, 4.0, "oscillation")
    output = tmp_path / "leaderboard.json"
    data = write_leaderboard(tmp_path, output)
    assert output.exists()
    assert data["total_models"] == 1
