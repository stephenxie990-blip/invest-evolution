from pathlib import Path
import json

from invest.leaderboard.engine import collect_cycle_records


def test_collect_cycle_records_excludes_config_snapshots(tmp_path: Path):
    (tmp_path / 'cycle_0001_config_snapshot.json').write_text(json.dumps({'initial_capital': 100000}), encoding='utf-8')
    (tmp_path / 'cycle_1.json').write_text(json.dumps({'cycle_id': 1, 'model_name': 'momentum', 'config_name': 'momentum_v1', 'params': {}, 'self_assessment': {}, 'return_pct': 1.0}), encoding='utf-8')
    records = collect_cycle_records(tmp_path)
    assert len(records) == 1
    assert records[0]['cycle_id'] == 1


def test_collect_cycle_records_excludes_nested_config_snapshot_directory(tmp_path: Path):
    snapshot_dir = tmp_path / 'run_a' / 'config_snapshots'
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / 'cycle_0001.json').write_text(
        json.dumps({'cycle_id': 1, 'model_name': 'unknown', 'config_name': 'config_snapshots'}),
        encoding='utf-8',
    )
    run_dir = tmp_path / 'run_a'
    (run_dir / 'cycle_1.json').write_text(
        json.dumps({'cycle_id': 1, 'model_name': 'momentum', 'config_name': 'momentum_v1', 'params': {}, 'self_assessment': {}, 'return_pct': 1.0}),
        encoding='utf-8',
    )

    records = collect_cycle_records(tmp_path)

    assert len(records) == 1
    assert records[0]['config_name'] == 'momentum_v1'
