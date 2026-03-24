from pathlib import Path
import json

from invest_evolution.investment.governance.engine import collect_cycle_records, write_leaderboard


def test_collect_cycle_records_excludes_config_snapshots(tmp_path: Path):
    (tmp_path / 'cycle_0001_config_snapshot.json').write_text(json.dumps({'initial_capital': 100000}), encoding='utf-8')
    (tmp_path / 'cycle_1.json').write_text(json.dumps({'cycle_id': 1, 'manager_id': 'momentum', 'manager_config_ref': 'momentum_v1', 'params': {}, 'self_assessment': {}, 'return_pct': 1.0}), encoding='utf-8')
    records = collect_cycle_records(tmp_path)
    assert len(records) == 1
    assert records[0]['cycle_id'] == 1


def test_collect_cycle_records_excludes_nested_config_snapshot_directory(tmp_path: Path):
    snapshot_dir = tmp_path / 'run_a' / 'config_snapshots'
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / 'cycle_0001.json').write_text(
        json.dumps({'cycle_id': 1, 'manager_id': 'unknown', 'manager_config_ref': 'config_snapshots'}),
        encoding='utf-8',
    )
    run_dir = tmp_path / 'run_a'
    (run_dir / 'cycle_1.json').write_text(
        json.dumps({'cycle_id': 1, 'manager_id': 'momentum', 'manager_config_ref': 'momentum_v1', 'params': {}, 'self_assessment': {}, 'return_pct': 1.0}),
        encoding='utf-8',
    )

    records = collect_cycle_records(tmp_path)

    assert len(records) == 1
    assert records[0]['manager_config_ref'] == 'momentum_v1'


def test_write_leaderboard_excludes_state_snapshots_directory(tmp_path: Path):
    snapshot_dir = tmp_path / 'state' / 'snapshots'
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for cycle_id in range(1, 4):
        (snapshot_dir / f'cycle_{cycle_id:04d}.json').write_text(
            json.dumps({'position_size_pct': 0.2, 'default_manager_id': 'momentum'}),
            encoding='utf-8',
        )

    run_dir = tmp_path / 'output'
    run_dir.mkdir(parents=True, exist_ok=True)
    for cycle_id in range(1, 4):
        (run_dir / f'cycle_{cycle_id}.json').write_text(
            json.dumps(
                {
                    'cycle_id': cycle_id,
                    'manager_id': 'momentum',
                    'manager_config_ref': 'momentum_v1',
                    'params': {},
                    'self_assessment': {'regime': 'bull', 'sharpe_ratio': 1.0, 'max_drawdown': 2.0},
                    'return_pct': 1.0,
                    'is_profit': True,
                    'benchmark_passed': True,
                }
            ),
            encoding='utf-8',
        )

    leaderboard = write_leaderboard(tmp_path)

    assert leaderboard['total_records'] == 3
    assert leaderboard['total_managers'] == 1
    assert leaderboard['eligible_managers'] == 1
    assert [entry['manager_id'] for entry in leaderboard['entries']] == ['momentum']


def test_collect_cycle_records_skips_oversized_cycle_payloads(tmp_path: Path):
    oversized = tmp_path / "release_shadow_gate_formal" / "cycle_23.json"
    oversized.parent.mkdir(parents=True, exist_ok=True)
    oversized.write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")

    run_dir = tmp_path / "bounded_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cycle_1.json").write_text(
        json.dumps(
            {
                "cycle_id": 1,
                "manager_id": "momentum",
                "manager_config_ref": "momentum_v1",
                "params": {},
                "self_assessment": {"regime": "bull", "sharpe_ratio": 1.0, "max_drawdown": 2.0},
                "return_pct": 1.0,
                "is_profit": True,
                "benchmark_passed": True,
            }
        ),
        encoding="utf-8",
    )

    records = collect_cycle_records(tmp_path)

    assert len(records) == 1
    assert records[0]["cycle_id"] == 1
